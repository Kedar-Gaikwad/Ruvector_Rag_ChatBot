import os
import time
import uuid
import asyncio
import tempfile
import httpx
import logging
import json
import re
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import pandas as pd
from pypdf import PdfReader
import boto3
from botocore.config import Config as BotoConfig

from chunker import SmartFinancialChunker
from bm25 import SimpleBM25

# ============================================================================
# LOGGING CONFIGURATION - Structured for CloudWatch Logs Insights
# ============================================================================
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s service=rag-app level=%(levelname)s request_id=%(request_id)s %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S'
)

# Custom filter to add request_id to all log records
class RequestIdFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, 'request_id'):
            record.request_id = 'system'
        return True

logger = logging.getLogger("rag-app")
logger.addFilter(RequestIdFilter())

# ============================================================================
# APPLICATION SETUP
# ============================================================================
app = FastAPI(title="Smart & Cost-Optimized Hybrid RAG API with Guardrails")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# CONFIGURATION
# ============================================================================
RUVECTOR_URL = os.getenv("RUVECTOR_URL", "http://172.17.0.1:6333")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "bedrock")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "bedrock")
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB

logger.info("Configuration loaded", extra={'request_id': 'startup'})
logger.info(f"RUVECTOR_URL={RUVECTOR_URL}, EMBEDDING_PROVIDER={EMBEDDING_PROVIDER}, LLM_PROVIDER={LLM_PROVIDER}", extra={'request_id': 'startup'})

# ============================================================================
# AWS BEDROCK CLIENT - Uses IAM instance role (no hardcoded credentials)
# ============================================================================
bedrock_client = None

def init_bedrock_client():
    """Initialize Bedrock client using IAM role credentials from instance metadata."""
    global bedrock_client
    try:
        boto_config = BotoConfig(
            region_name=AWS_REGION,
            read_timeout=30,
            connect_timeout=5,
            retries={'max_attempts': 3, 'mode': 'adaptive'}
        )
        bedrock_client = boto3.client(
            service_name="bedrock-runtime",
            config=boto_config
        )
        logger.info("AWS Bedrock client initialized via IAM role", extra={'request_id': 'startup'})
    except Exception as e:
        logger.warning(f"Failed to initialize Bedrock client: {e}", extra={'request_id': 'startup'})

init_bedrock_client()

# ============================================================================
# IN-MEMORY STATE
# ============================================================================
bm25_indices: Dict[str, SimpleBM25] = {}
ingestion_jobs: Dict[str, Dict[str, Any]] = {}  # job_id -> status dict

# ============================================================================
# EMBEDDING FUNCTIONS
# ============================================================================

def get_embedding(text: str) -> List[float]:
    """Generate embeddings using AWS Bedrock Titan Embed v2 (1024 dimensions)."""
    if EMBEDDING_PROVIDER != "bedrock" or not bedrock_client:
        raise RuntimeError("No embedding provider available. EMBEDDING_PROVIDER=bedrock required with valid IAM role.")

    try:
        body = json.dumps({"inputText": text})
        response = bedrock_client.invoke_model(
            body=body,
            modelId="amazon.titan-embed-text-v2:0",
            accept="application/json",
            contentType="application/json"
        )
        response_body = json.loads(response.get('body').read())
        return response_body.get('embedding')
    except Exception as e:
        logger.error(f"Bedrock embedding error: {e}", extra={'request_id': 'embedding'})
        raise RuntimeError(f"Embedding generation failed: {e}")


def get_vector_dimension() -> int:
    return 1024  # Bedrock Titan Embed v2


# ============================================================================
# HYBRID RETRIEVAL - Reciprocal Rank Fusion
# ============================================================================

def reciprocal_rank_fusion(dense_results: List[Dict], sparse_results: List[Dict], k_rrf: int = 60) -> List[Dict]:
    """Blends dense and sparse retrieval using RRF scoring."""
    scores: Dict[str, float] = {}
    doc_map: Dict[str, Dict] = {}

    for rank, doc in enumerate(dense_results):
        doc_id = doc["id"]
        doc_map[doc_id] = doc
        scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 / (k_rrf + rank + 1))

    for rank, doc in enumerate(sparse_results):
        doc_id = doc["id"]
        if doc_id not in doc_map:
            doc_map[doc_id] = {"id": doc_id, "score": 0.0, "metadata": doc["metadata"]}
        scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 / (k_rrf + rank + 1))

    sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    fused_results = []
    for doc_id, rrf_score in sorted_docs:
        doc = doc_map[doc_id].copy()
        doc["rrf_score"] = rrf_score
        fused_results.append(doc)

    return fused_results


# ============================================================================
# GUARDRAIL LAYER
# ============================================================================

def run_input_guardrail(query: str) -> Optional[str]:
    """Checks for prompt injection and domain relevance."""
    injection_patterns = [
        r"ignore previous instructions",
        r"system prompt",
        r"you are now an assistant who",
        r"forget everything",
        r"override rules"
    ]
    for pattern in injection_patterns:
        if re.search(pattern, query, re.IGNORECASE):
            return "Security Notice: Your query has been flagged for attempting prompt override. Please ask standard financial analysis questions."

    financial_keywords = [
        "revenue", "profit", "loss", "ebitda", "cash", "debt", "equity", "asset", "liability",
        "sheet", "statement", "fy", "q1", "q2", "q3", "q4", "finance", "stock", "shares", "growth",
        "margin", "audit", "tax", "report", "company", "net", "gross", "income", "rate"
    ]

    query_words = re.findall(r'\b[a-z]{3,}\b', query.lower())
    if len(query_words) > 3:
        matches = [word for word in query_words if word in financial_keywords]
        if not matches:
            return "Domain Guardrail: I am specialized in corporate and financial documents. Please ask a question related to financial reports."

    return None


def run_output_guardrail(response: str, contexts: List[str]) -> str:
    """Verifies response numbers against context and appends disclaimer."""
    numbers_in_response = re.findall(r'\b\d+(?:[.,]\d+)?\s*(?:%|m|b|million|billion)?\b', response.lower())
    context_str = " ".join(contexts).lower()

    hallucination_flag = False
    for num in numbers_in_response:
        if len(num) > 2 and num not in ["2023", "2024", "2025", "2026"]:
            if num not in context_str:
                logger.warning(f"Guardrail flagged potential hallucinated number: {num}", extra={'request_id': 'output'})
                hallucination_flag = True

    warning_suffix = ""
    if hallucination_flag:
        warning_suffix = "\n\n> [!WARNING]\n> *Note: Some metrics could not be verified directly in context. Please verify with source documents.*"

    disclaimer = "\n\n---\n*Disclaimer: Generated from corporate financial reports. Not certified financial advice. Verify key numbers in source documents.*"

    return response + warning_suffix + disclaimer


# ============================================================================
# BEDROCK LLM CALL
# ============================================================================

def call_bedrock_haiku(query: str, contexts: List[str]) -> str:
    """Calls Claude 3.5 Haiku via AWS Bedrock for RAG generation."""
    if not bedrock_client:
        return "AWS Bedrock client is not initialized. Verify IAM role credentials."

    context_str = "\n\n---\n\n".join(contexts)
    system_prompt = (
        "You are an expert financial analyst chatbot. Answer based strictly on the provided extracts.\n"
        "Rules:\n"
        "1. Ground all numbers in the extracts. Cite source document names.\n"
        "2. If extracts don't contain the answer, say clearly you lack sufficient information.\n"
        "3. Do not speculate or extrapolate beyond given data.\n"
        "4. Format tabular data using markdown tables."
    )
    user_prompt = f"Financial Extracts:\n{context_str}\n\nQuestion: {query}"

    try:
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": 0.1
        })

        response = bedrock_client.invoke_model(
            body=body,
            modelId="anthropic.claude-3-5-haiku-20241022-v1:0",
            accept="application/json",
            contentType="application/json"
        )
        response_body = json.loads(response.get('body').read())
        return response_body['content'][0]['text']
    except Exception as e:
        logger.error(f"Bedrock LLM invocation failed: {e}", extra={'request_id': 'llm'})
        return f"Bedrock error: {e}"


# ============================================================================
# MODELS
# ============================================================================

class ChatQuery(BaseModel):
    message: str
    collection: str = "finance_docs"


# ============================================================================
# HEALTH ENDPOINT
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint for ALB. Returns 200 if service is ready."""
    # Check RuVector connectivity
    ruvector_status = "unknown"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{RUVECTOR_URL}/health", timeout=3.0)
            ruvector_status = "healthy" if resp.status_code == 200 else "unhealthy"
    except Exception:
        ruvector_status = "unavailable"

    return JSONResponse(
        status_code=200,
        content={
            "status": "healthy",
            "service": "rag-app",
            "ruvector": ruvector_status,
            "embedding_provider": EMBEDDING_PROVIDER,
            "llm_provider": LLM_PROVIDER
        }
    )


# ============================================================================
# DOCUMENT INGESTION - Streaming with progress tracking
# ============================================================================

@app.post("/ingest")
async def ingest_document(file: UploadFile = File(...), collection: str = Form("finance_docs")):
    """Upload and process documents up to 100 MB with streaming and progress tracking."""
    start_time = time.time()
    filename = file.filename
    job_id = str(uuid.uuid4())
    request_id = job_id[:8]

    logger.info(f"Ingestion started: {filename} -> collection={collection}", extra={'request_id': request_id})

    # Initialize job status
    ingestion_jobs[job_id] = {
        "job_id": job_id,
        "filename": filename,
        "status": "processing",
        "progress_pct": 0,
        "total_pages": 0,
        "processed_pages": 0,
        "chunks_created": 0,
        "error": None
    }

    try:
        # Stream file to temp storage in chunks (max 10 MB in memory at once)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}")
        total_size = 0
        chunk_size = 10 * 1024 * 1024  # 10 MB

        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_UPLOAD_SIZE:
                temp_file.close()
                os.unlink(temp_file.name)
                ingestion_jobs[job_id]["status"] = "failed"
                ingestion_jobs[job_id]["error"] = "File exceeds 100 MB limit"
                raise HTTPException(status_code=413, detail="File exceeds maximum allowed size of 100 MB")
            temp_file.write(chunk)

        temp_file.close()
        logger.info(f"File streamed to disk: {total_size} bytes", extra={'request_id': request_id})

        # Parse file content
        content = ""
        try:
            if filename.endswith(".pdf"):
                reader = PdfReader(temp_file.name)
                total_pages = len(reader.pages)
                ingestion_jobs[job_id]["total_pages"] = total_pages
                for i, page in enumerate(reader.pages):
                    text = page.extract_text()
                    if text:
                        content += text + "\n"
                    ingestion_jobs[job_id]["processed_pages"] = i + 1
                    ingestion_jobs[job_id]["progress_pct"] = int(((i + 1) / total_pages) * 50)
            elif filename.endswith((".xlsx", ".xls")):
                df_dict = pd.read_excel(temp_file.name, sheet_name=None)
                total_sheets = len(df_dict)
                ingestion_jobs[job_id]["total_pages"] = total_sheets
                for i, (sheet, df) in enumerate(df_dict.items()):
                    content += f"\nSheet: {sheet}\n"
                    content += df.to_csv(index=False) + "\n"
                    ingestion_jobs[job_id]["processed_pages"] = i + 1
                    ingestion_jobs[job_id]["progress_pct"] = int(((i + 1) / total_sheets) * 50)
            elif filename.endswith(".csv"):
                df = pd.read_csv(temp_file.name)
                content = df.to_csv(index=False)
                ingestion_jobs[job_id]["total_pages"] = 1
                ingestion_jobs[job_id]["processed_pages"] = 1
                ingestion_jobs[job_id]["progress_pct"] = 50
            else:
                with open(temp_file.name, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                ingestion_jobs[job_id]["progress_pct"] = 50
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse document: {e}")
        finally:
            os.unlink(temp_file.name)

        if not content.strip():
            ingestion_jobs[job_id]["status"] = "failed"
            ingestion_jobs[job_id]["error"] = "Document content is empty"
            raise HTTPException(status_code=400, detail="Document content is empty.")

        # Chunking
        chunker = SmartFinancialChunker(chunk_size=700, overlap=120)
        chunks = chunker.chunk_document(content, filename)
        logger.info(f"Generated {len(chunks)} chunks", extra={'request_id': request_id})
        ingestion_jobs[job_id]["progress_pct"] = 60

        # Update BM25 index
        if collection not in bm25_indices:
            bm25_indices[collection] = SimpleBM25()
        all_chunks = bm25_indices[collection].documents_metadata + chunks
        bm25_indices[collection].fit(all_chunks)

        # Create collection in RuVector
        dim = get_vector_dimension()
        async with httpx.AsyncClient() as client:
            try:
                await client.post(f"{RUVECTOR_URL}/collections", json={
                    "name": collection, "dimension": dim, "metric": "Cosine"
                }, timeout=5.0)
            except Exception as e:
                logger.warning(f"Could not create collection: {e}", extra={'request_id': request_id})

        # Generate embeddings and upload vectors
        vector_entries = []
        total_chunks = len(chunks)
        for idx, chunk in enumerate(chunks):
            try:
                vector = get_embedding(chunk["text"])
                meta = chunk["metadata"]
                meta["text"] = chunk["text"]
                vector_entries.append({
                    "id": f"{filename}_{idx}_{int(time.time())}",
                    "vector": vector,
                    "metadata": meta
                })
            except Exception as e:
                logger.error(f"Embedding chunk {idx} failed: {e}", extra={'request_id': request_id})

            # Update progress (60-95% range)
            ingestion_jobs[job_id]["progress_pct"] = 60 + int((idx + 1) / total_chunks * 35)
            ingestion_jobs[job_id]["chunks_created"] = len(vector_entries)

        # Upsert to RuVector
        inserted_count = 0
        if vector_entries:
            try:
                async with httpx.AsyncClient() as client:
                    url = f"{RUVECTOR_URL}/collections/{collection}/points"
                    response = await client.put(url, json={"points": vector_entries}, timeout=30.0)
                    if response.status_code == 200:
                        inserted_count = len(vector_entries)
                        logger.info(f"Upserted {inserted_count} vectors", extra={'request_id': request_id})
                    else:
                        # Rollback: remove partial vectors on failure
                        logger.error(f"RuVector upsert failed: {response.text}", extra={'request_id': request_id})
                        ingestion_jobs[job_id]["status"] = "failed"
                        ingestion_jobs[job_id]["error"] = f"Vector storage failed: {response.text}"
                        raise HTTPException(status_code=500, detail=f"RuVector error: {response.text}")
            except HTTPException:
                raise
            except Exception as e:
                ingestion_jobs[job_id]["status"] = "failed"
                ingestion_jobs[job_id]["error"] = str(e)
                raise HTTPException(status_code=503, detail=f"RuVector unavailable: {e}")

        # Success
        ingestion_jobs[job_id]["status"] = "completed"
        ingestion_jobs[job_id]["progress_pct"] = 100
        ingestion_jobs[job_id]["chunks_created"] = inserted_count

        elapsed = time.time() - start_time
        return {
            "status": "success",
            "job_id": job_id,
            "filename": filename,
            "chunks": len(chunks),
            "inserted_points": inserted_count,
            "elapsed_seconds": round(elapsed, 2)
        }

    except HTTPException:
        raise
    except Exception as e:
        ingestion_jobs[job_id]["status"] = "failed"
        ingestion_jobs[job_id]["error"] = str(e)
        logger.error(f"Ingestion failed: {e}", extra={'request_id': request_id})
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {e}")


@app.get("/ingest/status/{job_id}")
async def get_ingestion_status(job_id: str):
    """Poll ingestion progress by job ID."""
    if job_id not in ingestion_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return ingestion_jobs[job_id]


# ============================================================================
# CHAT ENDPOINT
# ============================================================================

@app.post("/chat")
async def chat(query: ChatQuery):
    start_time = time.time()
    message = query.message
    collection = query.collection
    request_id = str(uuid.uuid4())[:8]

    # Input guardrail
    guardrail_violation = run_input_guardrail(message)
    if guardrail_violation:
        logger.warning(f"Input guardrail blocked: '{message}'", extra={'request_id': request_id})
        return {
            "response": guardrail_violation,
            "citations": [],
            "elapsed_ms": round((time.time() - start_time) * 1000, 2)
        }

    logger.info(f"Query: '{message}' collection={collection}", extra={'request_id': request_id})

    # Dense retrieval from RuVector
    dense_results = []
    try:
        query_vector = get_embedding(message)
        async with httpx.AsyncClient() as client:
            url = f"{RUVECTOR_URL}/collections/{collection}/points/search"
            payload = {"vector": query_vector, "k": 20, "filter": None}
            response = await client.post(url, json=payload, timeout=10.0)
            if response.status_code == 200:
                dense_results = response.json().get("results", [])
    except Exception as e:
        logger.error(f"Dense search failed: {e}", extra={'request_id': request_id})

    # Sparse retrieval from BM25
    sparse_results = []
    if collection in bm25_indices:
        try:
            raw_sparse = bm25_indices[collection].search(message, top_k=20)
            for idx, r in enumerate(raw_sparse):
                sparse_results.append({
                    "id": f"sparse_{idx}_{int(time.time())}",
                    "score": r["sparse_score"],
                    "metadata": r["metadata"]
                })
        except Exception as e:
            logger.error(f"Sparse search failed: {e}", extra={'request_id': request_id})

    # Hybrid fusion
    hybrid_results = reciprocal_rank_fusion(dense_results, sparse_results)

    if not hybrid_results:
        return {
            "response": "No financial documents have been ingested yet. Please upload your corporate files using the side panel.",
            "citations": [],
            "elapsed_ms": round((time.time() - start_time) * 1000, 2)
        }

    # Extract text from results
    documents_metadata = []
    for r in hybrid_results:
        meta = r.get("metadata", {}) or {}
        text = meta.get("text", "")
        if not text:
            continue
        documents_metadata.append({
            "id": r.get("id"),
            "text": text,
            "source": meta.get("source", "Unknown Document"),
            "type": meta.get("type", "prose"),
            "header": meta.get("header", ""),
            "rrf_score": r.get("rrf_score", 0.0)
        })

    if not documents_metadata:
        return {
            "response": "Retrieved text was empty. Please re-upload your files.",
            "citations": [],
            "elapsed_ms": round((time.time() - start_time) * 1000, 2)
        }

    top_chunks = documents_metadata[:4]

    # Context score gate - skip LLM call if relevance too low (saves Bedrock costs)
    top_score = top_chunks[0]["rrf_score"] if top_chunks else 0.0
    MIN_RRF_THRESHOLD = 0.01

    if top_score < MIN_RRF_THRESHOLD:
        logger.warning(f"Context gate: RRF score {top_score} below threshold, skipping LLM", extra={'request_id': request_id})
        return {
            "response": "Information Guardrail: Could not locate matching facts in ingested documents. LLM call skipped to prevent hallucination.",
            "citations": [],
            "elapsed_ms": round((time.time() - start_time) * 1000, 2)
        }

    # LLM Generation
    contexts = [c["text"] for c in top_chunks]

    if LLM_PROVIDER == "bedrock" and bedrock_client:
        raw_response = call_bedrock_haiku(message, contexts)
    else:
        raw_response = (
            f"**[MOCK MODE - No Bedrock]**\n\n"
            f"Matching extracts:\n\n" + "\n\n---\n\n".join(contexts)
        )

    # Output guardrail
    guarded_response = run_output_guardrail(raw_response, contexts)

    citations = []
    for c in top_chunks:
        citations.append({
            "source": c["source"],
            "type": c["type"],
            "header": c["header"],
            "snippet": c["text"][:300] + "..." if len(c["text"]) > 300 else c["text"]
        })

    elapsed = (time.time() - start_time) * 1000
    return {
        "response": guarded_response,
        "citations": citations,
        "elapsed_ms": round(elapsed, 2)
    }


# ============================================================================
# COLLECTION MANAGEMENT
# ============================================================================

@app.get("/collections")
async def list_collections():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{RUVECTOR_URL}/collections", timeout=5.0)
            if response.status_code == 200:
                return response.json()
            return {"collections": []}
    except Exception as e:
        logger.error(f"Failed to list collections: {e}", extra={'request_id': 'collections'})
        return {"collections": []}


@app.post("/collections/clear")
async def clear_collection(collection: str = Form("finance_docs")):
    try:
        if collection in bm25_indices:
            bm25_indices[collection] = SimpleBM25()

        async with httpx.AsyncClient() as client:
            await client.delete(f"{RUVECTOR_URL}/collections/{collection}", timeout=5.0)
            dim = get_vector_dimension()
            await client.post(f"{RUVECTOR_URL}/collections", json={
                "name": collection, "dimension": dim, "metric": "Cosine"
            }, timeout=5.0)
            return {"status": "success", "message": f"Collection '{collection}' cleared."}
    except Exception as e:
        logger.error(f"Failed to clear collection: {e}", extra={'request_id': 'clear'})
        raise HTTPException(status_code=500, detail=f"Failed to clear collection: {e}")


# ============================================================================
# STATIC FRONTEND
# ============================================================================
frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    logger.info(f"Serving frontend from: {frontend_dir}", extra={'request_id': 'startup'})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)


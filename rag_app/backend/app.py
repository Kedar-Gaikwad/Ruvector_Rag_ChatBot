import os
import time
import httpx
import logging
import json
import re
from typing import List, Dict, Any, Optional, Tuple
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import numpy as np
import pandas as pd
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer, CrossEncoder
from openai import OpenAI
import boto3

from chunker import SmartFinancialChunker
from bm25 import SimpleBM25

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("rag-app")

app = FastAPI(title="Smart & Cost-Optimized Hybrid RAG API with Guardrails")

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration from Environment
RUVECTOR_URL = os.getenv(
    "RUVECTOR_URL",
    "http://172.17.0.1:6333"
)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local")  # "local", "openai", "bedrock"

# Default to Bedrock Claude 3.5 Haiku as requested by user
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "bedrock")  # "bedrock", "openai", "mock"

# Initialize local models
logger.info("Loading local zero-cost embedding model (all-MiniLM-L6-v2)...")
local_embed_model = SentenceTransformer("all-MiniLM-L6-v2")

logger.info("Loading local zero-cost re-ranker model (ms-marco-MiniLM-L-6-v2)...")
local_reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")

# Global in-memory dictionary of BM25 indexes per collection
bm25_indices: Dict[str, SimpleBM25] = {}

# Initialize API clients
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
bedrock_client = None

if os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"):
    try:
        bedrock_client = boto3.client(
            service_name="bedrock-runtime",
            region_name=AWS_REGION
        )
        logger.info("AWS Bedrock client initialized successfully.")
    except Exception as e:
        logger.warning(f"Failed to initialize AWS Bedrock: {e}")

# Helper to get embeddings
def get_embedding(text: str) -> List[float]:
    if EMBEDDING_PROVIDER == "openai" and openai_client:
        try:
            response = openai_client.embeddings.create(
                input=[text],
                model="text-embedding-3-small"
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"OpenAI embedding error, falling back to local: {e}")
            
    elif EMBEDDING_PROVIDER == "bedrock" and bedrock_client:
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
            logger.error(f"Bedrock embedding error, falling back to local: {e}")
            
    # Default local embedding (dim = 384)
    vector = local_embed_model.encode(text)
    return vector.tolist()

# Helper to get vector dimensions
def get_vector_dimension() -> int:
    if EMBEDDING_PROVIDER == "openai":
        return 1536
    elif EMBEDDING_PROVIDER == "bedrock":
        return 1536
    return 384

# Reciprocal Rank Fusion (RRF) to blend Sparse (BM25) and Dense (RuVector) retrieval lists
def reciprocal_rank_fusion(dense_results: List[Dict], sparse_results: List[Dict], k_rrf: int = 60) -> List[Dict]:
    """
    Blends dense and sparse retrieval ranks using Reciprocal Rank Fusion.
    Ensures both exact keyword hits and conceptual meanings are accurately represented.
    """
    scores: Dict[str, float] = {}
    doc_map: Dict[str, Dict] = {}

    # Rank positions
    for rank, doc in enumerate(dense_results):
        doc_id = doc["id"]
        doc_map[doc_id] = doc
        scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 / (k_rrf + rank + 1))

    for rank, doc in enumerate(sparse_results):
        doc_id = doc["id"]
        # Merge metadata
        if doc_id not in doc_map:
            doc_map[doc_id] = {
                "id": doc_id,
                "score": 0.0,
                "metadata": doc["metadata"]
            }
        scores[doc_id] = scores.get(doc_id, 0.0) + (1.0 / (k_rrf + rank + 1))

    # Sort merged documents by fused RRF score
    sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    
    fused_results = []
    for doc_id, rrf_score in sorted_docs:
        doc = doc_map[doc_id].copy()
        doc["rrf_score"] = rrf_score
        fused_results.append(doc)

    return fused_results

# --- GUARDRAIL LAYER ---

def run_input_guardrail(query: str) -> Optional[str]:
    """
    Checks for malicious queries, prompt injections, or irrelevant domains.
    Returns refusal text if blocked, otherwise None.
    """
    # 1. Check for prompt injection keywords
    injection_patterns = [
        r"ignore previous instructions",
        r"system prompt",
        r"you are now an assistant who",
        r"forget everything",
        r"override rules"
    ]
    for pattern in injection_patterns:
        if re.search(pattern, query, re.IGNORECASE):
            return "Security Notice: Your query has been flagged by the system guardrail for attempting prompt override. Please ask standard financial analysis questions."

    # 2. Check for domain relevance (Financial documents)
    financial_keywords = [
        "revenue", "profit", "loss", "ebitda", "cash", "debt", "equity", "asset", "liability", 
        "sheet", "statement", "fy", "q1", "q2", "q3", "q4", "finance", "stock", "shares", "growth",
        "margin", "audit", "tax", "report", "company", "net", "gross", "income", "liability", "rate"
    ]
    
    # Check if any word in query matches financial domain keywords, or if query is very short
    query_words = re.findall(r'\b[a-z]{3,}\b', query.lower())
    if len(query_words) > 3:  # Only enforce on longer queries to avoid false positives on greetings
        matches = [word for word in query_words if word in financial_keywords]
        if not matches:
            return "Domain Guardrail: I am an intelligent chatbot specialized strictly in corporate and financial documents. Please ask a question related to financial reports, numbers, or balance sheets."
            
    return None

def run_context_gate_guardrail(top_rerank_score: float) -> bool:
    """
    Context Score Gate: If the semantic similarity score is below the threshold,
    we block the query before calling Bedrock to prevent hallucinations and save money.
    """
    # For cross-encoder/ms-marco-MiniLM-L-6-v2, scores below -5.0 generally indicate poor semantic match.
    MIN_RERANK_THRESHOLD = -5.0
    return top_rerank_score >= MIN_RERANK_THRESHOLD

def run_output_guardrail(response: str, contexts: List[str]) -> str:
    """
    Ensures that the LLM response does not hallucinate arbitrary numbers.
    Appends standard professional disclaimer.
    """
    # 1. Scan for large numbers in response and verify they exist in the matching source texts
    # Finds numbers like $124.5M, 1,450,000, 45.2%
    numbers_in_response = re.findall(r'\b\d+(?:[.,]\d+)?\s*(?:%|m|b|million|billion)?\b', response.lower())
    
    # Build single context string to verify against
    context_str = " ".join(contexts).lower()
    
    hallucination_flag = False
    for num in numbers_in_response:
        # Skip small helper numbers (like 1, 2, 2024, etc.)
        if len(num) > 2 and num not in ["2023", "2024", "2025", "2026"]:
            # If the specific metric/number is not physically present in context, flag it
            if num not in context_str:
                logger.warning(f"Guardrail flagged potential hallucinated number: {num}")
                hallucination_flag = True
                
    # If a flag occurred, append a citation warning
    warning_suffix = ""
    if hallucination_flag:
        warning_suffix = "\n\n> [!WARNING]\n> *Note: Some metrics in this answer could not be verified directly in the active context extracts. Please refer to the raw citations in the side panel for exact numbers.*"

    disclaimer = "\n\n---\n*Disclaimer: This response is generated based on corporate financial reports. This chatbot does not provide certified financial advice. Please verify key numbers directly inside source documents.*"
    
    return response + warning_suffix + disclaimer

# Helper to call Bedrock Claude 3.5 Haiku (Cost-Efficient and Intelligent)
def call_bedrock_haiku(query: str, contexts: List[str]) -> str:
    if not bedrock_client:
        return "AWS Bedrock client is not initialized. Please verify AWS credentials."

    context_str = "\n\n---\n\n".join(contexts)
    system_prompt = (
        "You are an expert financial analyst chatbot. Your task is to answer the user's question based strictly on the provided financial document extracts.\n"
        "Rules:\n"
        "1. Ground all numbers, percentages, and statements in the extracts. Cite the exact Source document name.\n"
        "2. If the document extracts do not contain the answer, say clearly that you do not have sufficient information in the loaded financial documents.\n"
        "3. Do not speculate or extrapolate financial metrics beyond what is given.\n"
        "4. Format tabular data or balance sheet extracts cleanly using markdown tables."
    )
    user_prompt = f"Financial Extracts:\n{context_str}\n\nQuestion: {query}"
    
    try:
        # Use Claude 3.5 Haiku - latest, smartest, cheapest Bedrock model for fast extraction
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": 0.1
        })
        
        response = bedrock_client.invoke_model(
            body=body,
            modelId="anthropic.claude-3-5-haiku-20241022-v1:0",  # Bedrock Claude 3.5 Haiku
            accept="application/json",
            contentType="application/json"
        )
        response_body = json.loads(response.get('body').read())
        return response_body['content'][0]['text']
    except Exception as e:
        logger.error(f"AWS Bedrock invocation failed: {e}")
        return f"AWS Bedrock error: {e}"

# Helper to call OpenAI GPT-4o-Mini
def call_openai_mini(query: str, contexts: List[str]) -> str:
    if not openai_client:
        return "OpenAI client is not initialized. Please verify API key."

    context_str = "\n\n---\n\n".join(contexts)
    system_prompt = (
        "You are an expert financial analyst chatbot. Your task is to answer the user's question based strictly on the provided financial document extracts.\n"
        "Rules:\n"
        "1. Ground all numbers, percentages, and statements in the extracts. Cite the exact Source document name.\n"
        "2. If the document extracts do not contain the answer, say clearly that you do not have sufficient information in the loaded financial documents.\n"
        "3. Do not speculate or extrapolate financial metrics beyond what is given.\n"
        "4. Format tabular data or balance sheet extracts cleanly using markdown tables."
    )
    user_prompt = f"Financial Extracts:\n{context_str}\n\nQuestion: {query}"
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"OpenAI error: {e}"

# Global initialization: Reload existing BM25 indices on startup if files exist in storage
# Since the server starts up memory:// by default, we fit the BM25 indices in RAM.

class ChatQuery(BaseModel):
    message: str
    collection: str = "finance_docs"

@app.post("/ingest")
async def ingest_document(file: UploadFile = File(...), collection: str = Form("finance_docs")):
    start_time = time.time()
    filename = file.filename
    logger.info(f"Ingesting file: {filename} into collection: {collection}")
    
    # 1. Parse File Content
    content = ""
    try:
        if filename.endswith(".pdf"):
            reader = PdfReader(file.file)
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    content += text + "\n"
        elif filename.endswith((".xlsx", ".xls")):
            df_dict = pd.read_excel(file.file, sheet_name=None)
            for sheet, df in df_dict.items():
                content += f"\nSheet: {sheet}\n"
                content += df.to_csv(index=False) + "\n"
        elif filename.endswith(".csv"):
            df = pd.read_csv(file.file)
            content = df.to_csv(index=False)
        else:
            content_bytes = await file.read()
            content = content_bytes.decode("utf-8", errors="ignore")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse document: {e}")
        
    if not content.strip():
        raise HTTPException(status_code=400, detail="Document content is empty.")
        
    # 2. Intelligent financial chunking
    chunker = SmartFinancialChunker(chunk_size=700, overlap=120)
    chunks = chunker.chunk_document(content, filename)
    logger.info(f"Generated {len(chunks)} chunks using Smart Financial Chunker.")
    
    # 3. Fit/Update Local Sparse BM25 Index for this collection
    if collection not in bm25_indices:
        bm25_indices[collection] = SimpleBM25()
        
    # Merge existing chunks in BM25 with new ones
    all_collection_chunks = bm25_indices[collection].documents_metadata + chunks
    bm25_indices[collection].fit(all_collection_chunks)
    logger.info(f"Updated local BM25 index. Total corpus size: {len(all_collection_chunks)} chunks.")

    # 4. Create collection in RuVector (auto-create if missing)
    dim = get_vector_dimension()
    async with httpx.AsyncClient() as client:
        try:
            await client.post(f"{RUVECTOR_URL}/collections", json={
                "name": collection,
                "dimension": dim,
                "metric": "Cosine"
            }, timeout=5.0)
        except Exception as e:
            logger.warning(f"Could not contact RuVector server to auto-create collection: {e}")
            
    # 5. Generate embeddings and upload to RuVector DB
    vector_entries = []
    for idx, chunk in enumerate(chunks):
        try:
            vector = get_embedding(chunk["text"])
            meta = chunk["metadata"]
            meta["text"] = chunk["text"]  # Persistent storage of text in metadata
            vector_entries.append({
                "id": f"{filename}_{idx}_{int(time.time())}",
                "vector": vector,
                "metadata": meta
            })
        except Exception as e:
            logger.error(f"Error embedding chunk {idx}: {e}")
            
    # Send points to RuVector
    inserted_count = 0
    if vector_entries:
        try:
            async with httpx.AsyncClient() as client:
                url = f"{RUVECTOR_URL}/collections/{collection}/points"
                response = await client.put(url, json={"points": vector_entries}, timeout=30.0)
                if response.status_code == 200:
                    inserted_count = len(vector_entries)
                    logger.info(f"Successfully upserted {inserted_count} dense points to RuVector.")
                else:
                    logger.error(f"RuVector upsert failed: {response.text}")
                    raise HTTPException(status_code=500, detail=f"RuVector Server returned error: {response.text}")
        except Exception as e:
            logger.error(f"Failed to upload points to RuVector: {e}")
            raise HTTPException(status_code=503, detail=f"RuVector Server is not responding. Details: {e}")
            
    elapsed = time.time() - start_time
    return {
        "status": "success",
        "filename": filename,
        "chunks": len(chunks),
        "inserted_points": inserted_count,
        "elapsed_seconds": round(elapsed, 2)
    }

@app.post("/chat")
async def chat(query: ChatQuery):
    start_time = time.time()
    message = query.message
    collection = query.collection
    
    # --- 1. RUN INPUT GUARDRAIL ---
    guardrail_violation = run_input_guardrail(message)
    if guardrail_violation:
        logger.warning(f"Input guardrail blocked query: '{message}'")
        return {
            "response": guardrail_violation,
            "citations": [],
            "elapsed_ms": round((time.time() - start_time) * 1000, 2)
        }

    logger.info(f"Query passed input guardrail: '{message}' in collection: {collection}")
    
    # --- 2. RETRIEVAL (HYBRID DENSE + SPARSE) ---
    
    # A. Dense Vector Retrieval (from RuVector DB)
    dense_results = []
    try:
        query_vector = get_embedding(message)
        async with httpx.AsyncClient() as client:
            url = f"{RUVECTOR_URL}/collections/{collection}/points/search"
            payload = {
                "vector": query_vector,
                "k": 20,  # Retrieve top 20 candidates
                "filter": None
            }
            response = await client.post(url, json=payload, timeout=10.0)
            if response.status_code == 200:
                search_data = response.json()
                dense_results = search_data.get("results", [])
                logger.info(f"Dense search retrieved {len(dense_results)} candidates.")
    except Exception as e:
        logger.error(f"Failed dense search against RuVector: {e}")
        
    # B. Sparse Keyword Retrieval (from Local BM25 Index)
    sparse_results = []
    if collection in bm25_indices:
        try:
            raw_sparse = bm25_indices[collection].search(message, top_k=20)
            # Reformat to match SearchResult schema
            for idx, r in enumerate(raw_sparse):
                sparse_results.append({
                    "id": f"sparse_{idx}_{int(time.time())}",
                    "score": r["sparse_score"],
                    "metadata": r["metadata"]
                })
            logger.info(f"Sparse search retrieved {len(sparse_results)} candidates.")
        except Exception as e:
            logger.error(f"Failed sparse search: {e}")

    # C. Blend lists using Reciprocal Rank Fusion (RRF)
    hybrid_results = reciprocal_rank_fusion(dense_results, sparse_results)
    logger.info(f"Hybrid retrieval combined list: {len(hybrid_results)} unique candidates.")

    if not hybrid_results:
        return {
            "response": "No financial documents have been ingested yet. Please upload your corporate files (PDF/CSV/Excel) using the side panel to begin!",
            "citations": [],
            "elapsed_ms": round((time.time() - start_time) * 1000, 2)
        }
        
    # --- 3. LOCAL ZERO-COST RERANKING ---
    pairs = []
    documents_metadata = []
    
    for r in hybrid_results:
        meta = r.get("metadata", {}) or {}
        text = meta.get("text", "")
        if not text:
            continue
        
        pairs.append([message, text])
        documents_metadata.append({
            "id": r.get("id"),
            "text": text,
            "source": meta.get("source", "Unknown Document"),
            "type": meta.get("type", "prose"),
            "header": meta.get("header", "")
        })
        
    if not pairs:
        return {
            "response": "Retrieve matching text was empty. Please re-upload your files.",
            "citations": [],
            "elapsed_ms": round((time.time() - start_time) * 1000, 2)
        }
        
    # Calculate cross-encoder scores locally on CPU
    rerank_scores = local_reranker.predict(pairs)
    for idx, score in enumerate(rerank_scores):
        documents_metadata[idx]["rerank_score"] = float(score)
        
    # Sort descending by rerank score
    documents_metadata.sort(key=lambda x: x["rerank_score"], reverse=True)
    top_chunks = documents_metadata[:4]
    
    # --- 4. CONTEXT SCORE GATE GUARDRAIL ---
    top_score = top_chunks[0]["rerank_score"] if top_chunks else -100.0
    logger.info(f"Rerank validation - Top match score: {top_score}")
    
    if not run_context_gate_guardrail(top_score):
        logger.warning(f"Context gate blocked prompt. Score {top_score} falls below threshold. Bypassed Bedrock API call.")
        return {
            "response": "Information Guardrail: Based on a comprehensive semantic and keyword check of all ingested documents, I could not locate any matching facts or numbers that answer your question. I declined calling the model to prevent hallucinated numbers.",
            "citations": [],
            "elapsed_ms": round((time.time() - start_time) * 1000, 2)
        }
        
    # --- 5. LLM GENERATION (BEDROCK CLAUDE 3.5 HAIKU / OPENAI) ---
    contexts = [c["text"] for c in top_chunks]
    
    # Call appropriate LLM
    if LLM_PROVIDER == "bedrock" and bedrock_client:
        raw_response = call_bedrock_haiku(message, contexts)
    elif LLM_PROVIDER == "openai" and openai_client:
        raw_response = call_openai_mini(message, contexts)
    else:
        # Fallback offline mock response
        raw_response = (
            f"**[FREE LOCAL MODE - NO Bedrock/OpenAI Credentials Configured]**\n\n"
            f"Here are the exact matching extracts found in the database:\n\n"
            + "\n\n---\n\n".join(contexts)
        )
        
    # --- 6. RUN OUTPUT GUARDRAIL ---
    guarded_response = run_output_guardrail(raw_response, contexts)
    
    # Prepare citations
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

@app.get("/collections")
async def list_collections():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{RUVECTOR_URL}/collections", timeout=5.0)
            if response.status_code == 200:
                return response.json()
            return {"collections": []}
    except Exception as e:
        logger.error(f"Failed to list collections from RuVector: {e}")
        return {"collections": ["finance_docs"]}

@app.post("/collections/clear")
async def clear_collection(collection: str = Form("finance_docs")):
    try:
        # Clear local BM25 index
        if collection in bm25_indices:
            bm25_indices[collection] = SimpleBM25()
            logger.info(f"Cleared local BM25 index for collection '{collection}'.")
            
        async with httpx.AsyncClient() as client:
            await client.delete(f"{RUVECTOR_URL}/collections/{collection}", timeout=5.0)
            dim = get_vector_dimension()
            await client.post(f"{RUVECTOR_URL}/collections", json={
                "name": collection,
                "dimension": dim,
                "metric": "Cosine"
            }, timeout=5.0)
            return {"status": "success", "message": f"Collection '{collection}' cleared."}
    except Exception as e:
        logger.error(f"Failed to clear collection: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clear collection: {e}")

# Serve UI
frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    logger.info(f"Serving static frontend from: {frontend_dir}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)

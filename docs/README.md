# Technical Overview — RuVector RAG ChatBot

## System Summary

A financial document analysis system that ingests PDFs, spreadsheets, and tax forms, then answers questions using hybrid vector + keyword retrieval backed by AWS Bedrock.

**Stack:**
- **FastAPI** backend — ingestion, retrieval, guardrails, LLM orchestration
- **Qdrant v1.9.2** — vector database (HNSW, cosine similarity)
- **AWS Bedrock** — Titan Embed Text v2 (embeddings) + Claude Haiku 4.5 (generation)
- **Amazon Textract** — OCR fallback for scanned PDFs and form field extraction
- **Glassmorphic frontend** — single-page chat UI with polling-based upload progress

---

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | ALB health check — returns 200 with Qdrant + Bedrock status |
| `/health/bedrock` | GET | Deep Bedrock check — calls Titan Embed, returns dimension count |
| `/debug/extract` | POST | Shows what each PDF extraction layer produces (AcroForm, pypdf, Textract) |
| `/ingest` | POST | Upload document — returns `job_id` immediately, processes in background |
| `/ingest/status/{job_id}` | GET | Poll ingestion progress (pct, pages, chunks) |
| `/chat` | POST | Hybrid RAG query — returns response + citations |
| `/collections` | GET | List Qdrant collections |
| `/collections/clear` | POST | Delete and recreate a collection |

---

## PDF Extraction — Three-Layer Strategy

The system handles all PDF types without requiring pre-processing:

```
PDF File
    │
    ├─ Layer 1: AcroForm Fields (document-level)
    │   pypdf reader.get_fields() extracts /AcroForm values
    │   Works for any filled interactive PDF form (W-9, 1099, etc.)
    │   Added as a standalone "page 0" chunk — not tied to any page number
    │
    ├─ Layer 2: pypdf Layout Extraction (native text PDFs)
    │   page.extract_text(extraction_mode="layout") per page
    │   Better spatial ordering than default extraction
    │   If page yields < 50 chars → treated as scanned → Layer 3
    │
    └─ Layer 3: Amazon Textract (scanned/image pages)
        AnalyzeDocument with FORMS + TABLES feature types
        Extracts key-value pairs from form fields
        Extracts LINE blocks for prose content
        Requires textract:AnalyzeDocument IAM permission
```

**Why three layers?** A single PDF can mix native text pages (instructions) with scanned pages (filled form). Each page is evaluated independently.

---

## Document Chunking — Form-Aware

`SmartFinancialChunker` detects document type and routes accordingly:

**Form detection** (`is_form_document`): looks for W-9, W-2, 1099, 1040, TIN, SSN, EIN, "Taxpayer Identification", "Part I/II" patterns. If ≥2 match → form path.

**Form path** (`chunk_page`):
- Each page chunked independently (filled values on page 1 don't get buried by 5 pages of instructions)
- Every line kept — no filtering — short values like `Sai vishnu Enterprise` or `699-78-9262` are preserved
- 3-line overlap between chunks so field labels stay with their values

**Report path** (standard):
- Table rows extracted with header context
- Prose chunked in 700-char sliding windows with 120-char overlap
- Paragraphs with ≥6 pipes skipped (pure table data handled by table extractor)

---

## Hybrid Retrieval

```
Query
  │
  ├─ Dense: Titan Embed → Qdrant cosine search (top 20)
  │
  ├─ Sparse: BM25 in-memory index (top 20)
  │
  └─ RRF Fusion: score = Σ 1/(60 + rank)
       │
       └─ Top 4 chunks → Claude Haiku → Response
```

**Qdrant REST API used:**
- `PUT /collections/{name}` — create/update with `{"vectors": {"size": 1024, "distance": "Cosine"}}`
- `PUT /collections/{name}/points` — upsert with UUID IDs and `payload` field (not `metadata`)
- `POST /collections/{name}/points/search` — search with `{"vector": [...], "limit": 20, "with_payload": true}`
- `GET /healthz` — liveness probe (not `/health`)

---

## Guardrail System

| Layer | When | What |
|-------|------|------|
| **Input** | Before retrieval | Blocks prompt injection patterns; enforces domain relevance (financial + document/form keywords) |
| **Context Gate** | After retrieval | Skips LLM if top RRF score < 0.01 (saves Bedrock cost) |
| **Output** | After generation | Flags numbers not found in context; appends financial disclaimer |

**Domain keywords** cover both financial reports (`revenue`, `ebitda`, `margin`) and document extraction (`name`, `entity`, `ssn`, `tin`, `what`, `extract`, `w-9`).

---

## Ingestion Flow (Async)

```
POST /ingest
    │
    ├─ Stream file to disk (10 MB chunks, max 100 MB)
    ├─ Register job_id with status="processing"
    ├─ Return {"status":"accepted","job_id":"..."} immediately
    │
    └─ Background task:
        ├─ extract_pdf() — 3-layer extraction
        ├─ SmartFinancialChunker.chunk_document()
        ├─ BM25 index update
        ├─ Qdrant collection create/ensure
        ├─ Bedrock Titan embeddings (batches of 10)
        └─ Qdrant upsert (batches of 10)

GET /ingest/status/{job_id}
    └─ Returns progress_pct, chunks_created, status, error
```

The async pattern avoids ALB 504 timeouts. The ALB idle timeout is set to 300s as a safety net.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RUVECTOR_URL` | `http://172.17.0.1:6333` | Qdrant REST endpoint |
| `EMBEDDING_PROVIDER` | `bedrock` | Must be `bedrock` |
| `LLM_PROVIDER` | `bedrock` | `bedrock` or `mock` |
| `AWS_REGION` | `us-east-1` | AWS region for all service calls |
| `AWS_DEFAULT_REGION` | `us-east-1` | boto3 fallback region |

On EC2, credentials come from the IAM instance role via IMDS (`169.254.169.254`). The container runs with `--network host` to reach IMDS. On local dev, pass `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` via `.env` file.

---

## Models Used

| Model | ID | Purpose |
|-------|----|---------|
| Titan Embed Text v2 | `amazon.titan-embed-text-v2:0` | 1024-dim embeddings |
| Claude Haiku 4.5 | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | RAG generation (US Cross-Region Inference Profile) |

---

## Local Development

```bash
# Copy credentials template
cp .env.example .env
# Edit .env with your AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY

# Start everything
docker compose --env-file .env up --build

# Verify
curl http://localhost:8000/health
```

The `docker-compose.yml` mirrors the AWS architecture: `qdrant` service (v1.9.2) + `rag-chatbot` service. Qdrant data persists in a named volume `qdrant_data`.

---

## Deploy Updates (No Terraform)

After pushing code changes to GitHub:

```bash
# Option 1: SSH directly
cd /home/ubuntu/rag_app && sudo git pull
sudo docker build -t rag-chatbot -f Dockerfile .
sudo docker stop rag-chatbot && sudo docker rm rag-chatbot
sudo docker run -d --name rag-chatbot --restart unless-stopped --network host \
  -e RUVECTOR_URL="http://<qdrant-private-ip>:6333" \
  -e EMBEDDING_PROVIDER="bedrock" -e LLM_PROVIDER="bedrock" \
  -e AWS_REGION="us-east-1" -e AWS_DEFAULT_REGION="us-east-1" \
  rag-chatbot

# Option 2: deploy.sh via SSM (no SSH needed)
./deploy.sh <instance-id> <qdrant-private-ip>
```

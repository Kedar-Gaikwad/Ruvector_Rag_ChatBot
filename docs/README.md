# Technical Overview — RuVector RAG ChatBot

## System Summary

The RuVector RAG ChatBot is a financial document analysis system that combines:

1. **RuVector** — A Rust-based vector database using HNSW indexing for approximate nearest-neighbor search
2. **FastAPI Backend** — Handles document ingestion, hybrid retrieval, guardrails, and LLM orchestration
3. **AWS Bedrock** — Managed AI services for embedding generation (Titan) and text generation (Claude 3.5 Haiku)
4. **Glassmorphic Frontend** — Single-page chat UI with real-time progress, citation exploration, and session persistence

The system is designed for corporate financial document analysis — balance sheets, income statements, annual reports — with built-in guardrails that prevent hallucination, enforce domain relevance, and gate LLM calls based on retrieval confidence.

---

## Component Descriptions

### 1. RAG App (FastAPI Backend)

**File:** `rag_app/backend/app.py`

The core application server exposing these endpoints:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | ALB health check — returns 200 with RuVector connectivity status |
| `/ingest` | POST | Upload documents (PDF/Excel/CSV/TXT, up to 100 MB) |
| `/ingest/status/{job_id}` | GET | Poll ingestion progress (percentage, pages processed) |
| `/chat` | POST | Query the RAG pipeline — hybrid retrieval + LLM generation |
| `/collections` | GET | List vector collections in RuVector |
| `/collections/clear` | POST | Delete and recreate a collection |

**Key Design Choices:**

- **No hardcoded credentials** — Uses IAM instance role for Bedrock authentication
- **Streaming ingestion** — Files saved to disk in 10 MB chunks to stay within t3.medium memory (4 GB)
- **Adaptive retries** — Bedrock client uses exponential backoff with 3 max attempts
- **Structured logging** — ISO 8601 timestamps, service name, request ID for CloudWatch Insights queries

### 2. RuVector Vector Database

**Image:** `ruvnet/ruvector:latest`

A containerized vector database providing:

- **HNSW Index** — Hierarchical Navigable Small World graph for fast approximate nearest-neighbor search
- **Cosine Similarity** — Default metric for financial text embeddings
- **REST API** — Create/delete collections, upsert points, similarity search
- **Persistent Storage** — Data directory mounted from dedicated EBS volume

**Port:** 6333 (restricted to RAG App security group in production)

### 3. Hybrid Retrieval Pipeline

The system uses two complementary retrieval methods blended via Reciprocal Rank Fusion (RRF):

```
Query → [Dense Search (RuVector)] ──┐
                                     ├── RRF Fusion → Top-4 → LLM
Query → [Sparse Search (BM25)]   ──┘
```

- **Dense (Semantic):** Query embedding compared against stored vectors via cosine similarity. Captures conceptual meaning.
- **Sparse (Keyword):** BM25 term-frequency scoring against raw text. Captures exact keyword matches.
- **RRF Blending:** `score = Σ 1/(k + rank)` with k=60. Ensures both exact matches and semantic results are represented.

### 4. Guardrail System

Three-layer protection:

| Layer | When | What |
|-------|------|------|
| **Input Guardrail** | Before retrieval | Blocks prompt injection, enforces financial domain |
| **Context Gate** | After retrieval | Skips LLM if RRF score < 0.01 (saves Bedrock costs) |
| **Output Guardrail** | After generation | Flags numbers not found in context, appends disclaimer |

### 5. Smart Financial Chunker

**File:** `rag_app/backend/chunker.py`

Intelligent document segmentation that:

- Preserves table structures (detects column alignment, pipe tables, CSV rows)
- Respects section headers (keeps header with following content)
- Uses 700-token chunks with 120-token overlap
- Tags each chunk with metadata: source file, type (prose/table_row), header context, page number

### 6. Frontend (Session-Persistent Chat UI)

**File:** `rag_app/frontend/index.html`

Single HTML file with:

- **Glassmorphic design** — Dark mode, blur effects, gradient accents
- **Session persistence** — Chat history stored in localStorage, survives reloads
- **Citation drawer** — Click citations to view source extracts in slide-over panel
- **Upload progress** — Real-time progress bar during document ingestion
- **Health monitoring** — Auto-polls VectorDB status every 10 seconds

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RUVECTOR_URL` | `http://172.17.0.1:6333` | RuVector REST API endpoint |
| `EMBEDDING_PROVIDER` | `bedrock` | Embedding backend (`bedrock`) |
| `LLM_PROVIDER` | `bedrock` | LLM backend (`bedrock` or `mock`) |
| `AWS_REGION` | `us-east-1` | AWS region for Bedrock API calls |

---

## Data Models

### Vector Point

```json
{
  "id": "annual_report_2024.pdf_42_1717000000",
  "vector": [0.012, -0.034, ...],  // 1024 dimensions
  "metadata": {
    "text": "Revenue for Q3 2024 was $142.5M...",
    "source": "annual_report_2024.pdf",
    "type": "prose",
    "header": "Revenue by Segment",
    "page": 12
  }
}
```

### Chat Response

```json
{
  "response": "Based on the annual report, Q3 2024 EBITDA was...",
  "citations": [
    {
      "source": "annual_report_2024.pdf",
      "type": "table_row",
      "header": "Quarterly EBITDA",
      "snippet": "Q3 2024: $142.5M..."
    }
  ],
  "elapsed_ms": 1823.4
}
```

### Ingestion Status

```json
{
  "job_id": "a1b2c3d4-...",
  "filename": "report.pdf",
  "status": "processing",
  "progress_pct": 65,
  "total_pages": 120,
  "processed_pages": 78,
  "chunks_created": 342,
  "error": null
}
```


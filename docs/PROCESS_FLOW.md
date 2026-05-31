# Process Flow — RuVector RAG ChatBot

---

## 1. Health Check Flow

```
ALB                    RAG App                  Qdrant          Bedrock
 │                        │                        │               │
 │──GET /health──────────▶│                        │               │
 │                        │──GET /healthz─────────▶│               │
 │                        │◀──200 OK───────────────│               │
 │                        │                        │               │
 │                        │──invoke_model("health")────────────────▶│
 │                        │◀──embedding[1024]──────────────────────│
 │                        │                        │               │
 │◀──200 OK──────────────│                        │               │
 │  {                     │                        │               │
 │    "status":"healthy", │                        │               │
 │    "qdrant":"healthy", │                        │               │
 │    "bedrock":"healthy (dim=1024)",              │               │
 │    "ruvector_url":"http://10.0.x.x:6333"       │               │
 │  }                     │                        │               │
```

The health endpoint always returns HTTP 200 (so ALB never marks it unhealthy due to a Bedrock blip). Component status is in the body. The Bedrock check makes a real Titan Embed call — if it returns 1024 dimensions, the full pipeline is working.

**Qdrant health path:** `/healthz` (Kubernetes liveness probe). Not `/health` — that returns a 404.

---

## 2. Document Ingestion Flow

### 2a. Upload Phase (synchronous, fast)

```
Browser              RAG App
   │                    │
   │──POST /ingest──────▶│
   │  (multipart file)  │
   │                    │──stream to temp file (10 MB chunks)
   │                    │──register job_id, status="processing"
   │◀──202 Accepted─────│
   │  {                 │
   │    "job_id":"...", │
   │    "status":"accepted"
   │  }                 │
```

Returns in < 1 second regardless of file size. No ALB timeout risk.

### 2b. Background Processing Phase

```
Background Task      PDF Extractor           Bedrock          Qdrant
      │                    │                    │                │
      │──extract_pdf()────▶│                    │                │
      │                    │                    │                │
      │  Layer 1: AcroForm fields               │                │
      │  (reader.get_fields() — any page)       │                │
      │                    │                    │                │
      │  Layer 2: pypdf layout per page         │                │
      │  (extraction_mode="layout")             │                │
      │                    │                    │                │
      │  Layer 3: Textract (if page < 50 chars) │                │
      │  (AnalyzeDocument FORMS+TABLES)         │                │
      │◀──(full_content, page_contents)─────────│                │
      │                    │                    │                │
      │──SmartFinancialChunker.chunk_document() │                │
      │  ├─ is_form_document()? → chunk_page()  │                │
      │  │   (per-page, every line kept)        │                │
      │  └─ else → prose + table chunking       │                │
      │                    │                    │                │
      │──PUT /collections/{name}────────────────────────────────▶│
      │  {"vectors":{"size":1024,"distance":"Cosine"}}           │
      │◀──200/201──────────────────────────────────────────────── │
      │                    │                    │                │
      │  FOR EACH BATCH (10 chunks):            │                │
      │  ├─ invoke_model(Titan Embed)──────────▶│                │
      │  │◀──embedding[1024]──────────────────── │                │
      │  │                                      │                │
      │  └─ PUT /collections/{name}/points──────────────────────▶│
      │     {"points":[{"id":uuid,"vector":[...],"payload":{}}]} │
      │◀──200/201──────────────────────────────────────────────── │
      │                    │                    │                │
      │  progress_pct = 55 + (batch/total × 40) │                │
```

### 2c. Progress Polling

```
Browser              RAG App
   │                    │
   │──GET /ingest/status/{job_id}──▶│
   │◀──{                            │
   │     "status":"processing",     │
   │     "progress_pct":72,         │
   │     "chunks_created":34,       │
   │     "total_pages":6            │
   │   }                            │
   │                    │
   │  (poll every 5s until status="completed" or "failed")
```

### Progress Phases

| Phase | Progress % | Description |
|-------|-----------|-------------|
| Upload received | 5% | File streamed to disk |
| PDF extraction | 5-45% | AcroForm + pypdf + Textract per page |
| Chunking | 55% | SmartFinancialChunker |
| Embedding + upsert | 55-95% | Bedrock Titan + Qdrant batches |
| Complete | 100% | All chunks inserted |

---

## 3. Chat Query Flow

```
Browser              RAG App                  Bedrock          Qdrant
   │                    │                        │                │
   │──POST /chat────────▶│                        │                │
   │  {"message":"..."}  │                        │                │
   │                    │                        │                │
   │                    │──[INPUT GUARDRAIL]      │                │
   │                    │  • injection patterns   │                │
   │                    │  • domain keywords      │                │
   │                    │  (financial + form)     │                │
   │◀──{guardrail msg}──│ (if blocked)            │                │
   │                    │                        │                │
   │                    │──invoke_model(Titan)───▶│                │
   │                    │◀──query_vector[1024]────│                │
   │                    │                        │                │
   │                    │──POST /points/search────────────────────▶│
   │                    │  {"vector":[...],"limit":20,"with_payload":true}
   │                    │◀──[{id,score,payload},...]──────────────│
   │                    │                        │                │
   │                    │──[BM25 search]          │                │
   │                    │  (in-memory, local)     │                │
   │                    │                        │                │
   │                    │──[RRF Fusion]           │                │
   │                    │  score = Σ 1/(60+rank)  │                │
   │                    │                        │                │
   │                    │──[CONTEXT GATE]         │                │
   │                    │  top_rrf < 0.01?        │                │
   │◀──{insufficient}───│ (skip LLM, save cost)  │                │
   │                    │                        │                │
   │                    │──invoke_model(Claude)──▶│                │
   │                    │  (top 4 chunks as ctx)  │                │
   │                    │◀──generated_text────────│                │
   │                    │                        │                │
   │                    │──[OUTPUT GUARDRAIL]     │                │
   │                    │  • number verification  │                │
   │                    │  • disclaimer append    │                │
   │                    │                        │                │
   │◀──200 OK───────────│                        │                │
   │  {                 │                        │                │
   │    "response":"...",│                       │                │
   │    "citations":[...],                       │                │
   │    "elapsed_ms":1823                        │                │
   │  }                 │                        │                │
```

---

## 4. PDF Extraction Decision Tree

```
PDF uploaded
    │
    ├─ reader.get_fields() → AcroForm fields?
    │   YES → add as "page 0" chunk (entity name, SSN, EIN, etc.)
    │   NO  → skip
    │
    └─ For each page:
        │
        ├─ pypdf layout extraction → len(text) >= 50?
        │   YES → use layout text
        │   NO  → try plain extraction → len(text) >= 50?
        │           YES → use plain text
        │           NO  → SCANNED PAGE
        │                   │
        │                   └─ Textract AnalyzeDocument(FORMS, TABLES)
        │                       ├─ KEY_VALUE_SET blocks → form field pairs
        │                       └─ LINE blocks → prose text
        │
        └─ page_contents.append((page_num, text))
```

---

## 5. Chunking Decision Tree

```
chunk_document(text, doc_name, page_contents)
    │
    ├─ is_form_document(text)?
    │   Checks for: W-9, W-2, 1099, 1040, TIN, SSN, EIN,
    │   "Taxpayer Identification", "Part I/II", "Request for Taxpayer"
    │   (≥2 patterns must match)
    │
    │   YES → Form path:
    │           For each (page_num, page_text) in page_contents:
    │               chunk_page(page_text, doc_name, page_num)
    │               ├─ Keep every line (no filtering)
    │               ├─ 700-char windows
    │               └─ 3-line overlap (label stays with value)
    │
    └─ NO → Report path:
            ├─ parse_table_from_text() → table row chunks
            └─ Sliding prose chunks (700 chars, 120 overlap)
               Skip paragraphs with ≥6 pipes (pure table data)
```

---

## 6. Guardrail Decision Flow

```
User Query
    │
    ├─ Injection check (regex):
    │   "ignore previous instructions", "system prompt",
    │   "you are now", "forget everything", "override rules"
    │   → BLOCKED: security warning
    │
    ├─ Domain check (keyword match, only if query > 3 words):
    │   Financial: revenue, profit, ebitda, tax, margin, ...
    │   Document:  name, entity, ssn, tin, what, extract, w-9, ...
    │   → BLOCKED if zero matches: domain warning
    │
    ├─ Retrieval (dense + sparse + RRF)
    │
    ├─ Context gate: top_rrf_score < 0.01?
    │   → BLOCKED: "insufficient context" (LLM call skipped)
    │
    ├─ LLM generation (Claude Haiku 4.5)
    │
    └─ Output guardrail:
        ├─ Extract numbers from response
        ├─ Check each number exists in context
        ├─ If not found → append [!WARNING] notice
        └─ Always append financial disclaimer
```

---

## 7. Spot Instance Interruption Flow

```
T+0s:    AWS sends 2-minute interruption notice
         Container stops gracefully

T+2m:    Instance stopped
         ALB health checks start failing

T+3m30s: ALB marks target UNHEALTHY (3 × 30s failures)
         Traffic stops routing to RAG App

T+3m30s–T+5m: Spot fleet requests replacement instance

T+5m–T+10m: user_data/rag_app.sh executes:
            - Install Docker (~1 min)
            - Wait for Qdrant /healthz
            - git clone + docker build (~3 min)
            - docker run --network host
            - Health check passes

T+10m30s: ALB marks target HEALTHY (2 × 30s successes)
          Traffic resumes
```

**Data impact:** Zero. Qdrant runs on a separate On-Demand instance with a persistent EBS volume. All vector data is preserved across RAG App interruptions.

---

## 8. Collection Clear Flow

```
POST /collections/clear
    │
    ├─ Reset in-memory BM25 index for collection
    │
    ├─ httpx.AsyncClient #1:
    │   DELETE /collections/{name}
    │   (200 = deleted, 404 = didn't exist — both OK)
    │   Client closed ← important: Qdrant closes TCP after DELETE
    │
    └─ httpx.AsyncClient #2 (new connection):
        PUT /collections/{name}
        {"vectors": {"size": 1024, "distance": "Cosine"}}
        (200/201 = success)
```

**Why two separate clients?** Qdrant closes the TCP connection after a DELETE response. Reusing the same `httpx.AsyncClient` for the subsequent PUT causes "All connection attempts failed". Separate `async with` blocks create fresh connection pools.

---

## 9. End-to-End Latency

| Step | Duration | Notes |
|------|----------|-------|
| Input guardrail | < 1 ms | Regex, in-memory |
| Query embedding (Titan) | 100-300 ms | Via VPC endpoint |
| Dense search (Qdrant) | 5-20 ms | Same VPC |
| BM25 sparse search | 1-5 ms | In-memory |
| RRF fusion | < 1 ms | Arithmetic |
| Context gate | < 1 ms | Score comparison |
| LLM generation (Claude Haiku) | 800-2000 ms | Depends on output length |
| Output guardrail | 1-5 ms | Regex |
| **Total** | **~1-2.5 seconds** | |

Context gate short-circuits at ~200-400 ms for low-confidence queries, saving ~$0.003 per skipped LLM call.

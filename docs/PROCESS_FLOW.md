# Process Flow — RuVector RAG ChatBot

This document describes the end-to-end request lifecycle for all major operations in the system: health checks, document ingestion, chat queries, and error handling flows.

---

## Table of Contents

1. [Health Check Flow](#1-health-check-flow)
2. [Document Ingestion Flow](#2-document-ingestion-flow)
3. [Chat Query Flow](#3-chat-query-flow)
4. [Guardrail Decision Flow](#4-guardrail-decision-flow)
5. [Spot Instance Interruption Flow](#5-spot-instance-interruption-flow)
6. [Error Handling Flows](#6-error-handling-flows)
7. [Session Persistence Flow](#7-session-persistence-flow)

---

## 1. Health Check Flow

The ALB performs health checks every 30 seconds to determine if the RAG App can serve traffic.

```
ALB                          RAG App                       RuVector
 │                              │                              │
 │──GET /health────────────────▶│                              │
 │                              │──GET /health────────────────▶│
 │                              │◀─────────200 OK──────────────│
 │                              │                              │
 │◀────200 OK──────────────────-│                              │
 │  {                           │                              │
 │    "status": "healthy",      │                              │
 │    "ruvector": "healthy",    │                              │
 │    "embedding_provider":     │                              │
 │      "bedrock"               │                              │
 │  }                           │                              │
```

**Decision logic:**
- RAG App always returns 200 if the process is running (even if RuVector is down)
- The `ruvector` field indicates vector DB connectivity
- ALB marks target healthy after 2 consecutive 200 responses
- ALB marks target unhealthy after 3 consecutive failures (timeout or non-200)

---

## 2. Document Ingestion Flow

Large document processing with streaming, chunking, embedding, and vector storage.

```
User Browser              RAG App                    Bedrock               RuVector
     │                       │                          │                      │
     │──POST /ingest─────────▶│                          │                      │
     │  (multipart, ≤100MB)  │                          │                      │
     │                       │                          │                      │
     │                       │──[Stream to disk]────┐   │                      │
     │                       │  (10 MB chunks)      │   │                      │
     │                       │◀─────────────────────┘   │                      │
     │                       │                          │                      │
     │                       │──[Parse document]────┐   │                      │
     │                       │  PDF/Excel/CSV        │   │                      │
     │                       │  Track page progress  │   │                      │
     │                       │◀─────────────────────┘   │                      │
     │                       │                          │                      │
     │                       │──[Smart Chunking]────┐   │                      │
     │                       │  700 tokens, 120 overlap│  │                      │
     │                       │  Preserve tables      │   │                      │
     │                       │◀─────────────────────┘   │                      │
     │                       │                          │                      │
     │                       │──[Update BM25 Index]─┐   │                      │
     │                       │◀─────────────────────┘   │                      │
     │                       │                          │                      │
     │                       │──POST /collections───────┼─────────────────────▶│
     │                       │  (create if missing)     │                      │
     │                       │◀─────────────────────────┼──────────────────────│
     │                       │                          │                      │
     │                       │  ┌─── FOR EACH CHUNK ────┐                      │
     │                       │  │                       │                      │
     │                       │──┼──InvokeModel──────────▶│                      │
     │                       │  │  (Titan Embed v2)     │                      │
     │                       │◀─┼──[1024-dim vector]────│                      │
     │                       │  │                       │                      │
     │                       │  │  progress_pct = 60 + (idx/total × 35)        │
     │                       │  │                       │                      │
     │                       │  └───────────────────────┘                      │
     │                       │                          │                      │
     │                       │──PUT /collections/{name}/points────────────────▶│
     │                       │  (batch upsert all vectors)                     │
     │                       │◀────────────200 OK──────────────────────────────│
     │                       │                          │                      │
     │◀──200 OK──────────────│                          │                      │
     │  {                    │                          │                      │
     │    "job_id": "...",   │                          │                      │
     │    "chunks": 342,     │                          │                      │
     │    "inserted_points": │                          │                      │
     │      342              │                          │                      │
     │  }                    │                          │                      │
```

### Ingestion Progress Tracking

The client can poll `GET /ingest/status/{job_id}` during processing:

| Phase | Progress % | Description |
|-------|-----------|-------------|
| Streaming | 0% | File being received |
| Parsing | 0-50% | Pages/sheets extracted |
| Chunking | 50-60% | Smart financial chunking |
| Embedding | 60-95% | Bedrock Titan calls per chunk |
| Upserting | 95-100% | Vectors stored in RuVector |

### Failure Rollback

If the upsert to RuVector fails after partial embedding:
1. Job status set to `"failed"` with error message
2. Already-generated vectors are discarded (not sent to RuVector)
3. BM25 index may be partially updated (ephemeral, not critical)
4. Temp file is always cleaned up in `finally` block

---

## 3. Chat Query Flow

The complete RAG pipeline from user question to grounded answer.

```
User Browser              RAG App                    Bedrock               RuVector
     │                       │                          │                      │
     │──POST /chat───────────▶│                          │                      │
     │  {"message": "...",   │                          │                      │
     │   "collection": "..."}│                          │                      │
     │                       │                          │                      │
     │                       │──[INPUT GUARDRAIL]───┐   │                      │
     │                       │  • Injection check    │   │                      │
     │                       │  • Domain relevance   │   │                      │
     │                       │◀─────────────────────┘   │                      │
     │                       │                          │                      │
     │                       │  IF BLOCKED:             │                      │
     │◀──{guardrail msg}─────│                          │                      │
     │                       │                          │                      │
     │                       │──InvokeModel─────────────▶│                      │
     │                       │  (embed query, 1024-dim) │                      │
     │                       │◀─[query_vector]──────────│                      │
     │                       │                          │                      │
     │                       │──POST /points/search──────┼─────────────────────▶│
     │                       │  {vector, k=20}          │                      │
     │                       │◀─[dense_results]─────────┼──────────────────────│
     │                       │                          │                      │
     │                       │──[BM25 SEARCH]───────┐   │                      │
     │                       │  (local, in-memory)   │   │                      │
     │                       │◀─[sparse_results]────┘   │                      │
     │                       │                          │                      │
     │                       │──[RRF FUSION]────────┐   │                      │
     │                       │  score = Σ 1/(60+rank)│   │                      │
     │                       │  Sort by fused score  │   │                      │
     │                       │◀─[hybrid_results]────┘   │                      │
     │                       │                          │                      │
     │                       │──[CONTEXT GATE]──────┐   │                      │
     │                       │  top_rrf_score ≥ 0.01?│   │                      │
     │                       │◀─────────────────────┘   │                      │
     │                       │                          │                      │
     │                       │  IF SCORE < 0.01:        │                      │
     │◀──{insufficient ctx}──│  (Skip Bedrock, save $) │                      │
     │                       │                          │                      │
     │                       │──InvokeModel─────────────▶│                      │
     │                       │  (Claude 3.5 Haiku)      │                      │
     │                       │  system: financial analyst│                      │
     │                       │  user: extracts + query  │                      │
     │                       │  max_tokens: 1000        │                      │
     │                       │  temperature: 0.1        │                      │
     │                       │◀─[generated response]────│                      │
     │                       │                          │                      │
     │                       │──[OUTPUT GUARDRAIL]──┐   │                      │
     │                       │  • Verify numbers     │   │                      │
     │                       │  • Append disclaimer  │   │                      │
     │                       │◀─────────────────────┘   │                      │
     │                       │                          │                      │
     │◀──200 OK──────────────│                          │                      │
     │  {                    │                          │                      │
     │    "response": "...", │                          │                      │
     │    "citations": [...],│                          │                      │
     │    "elapsed_ms": 1823 │                          │                      │
     │  }                    │                          │                      │
```

### Retrieval Strategy Detail

**Dense Search (Semantic):**
- Query text → Bedrock Titan Embed v2 → 1024-dim vector
- Vector sent to RuVector for cosine similarity search
- Returns top-20 nearest neighbors with scores

**Sparse Search (BM25):**
- Query tokenized into terms
- BM25 scoring against all chunk texts in the collection
- Returns top-20 by term frequency relevance

**Reciprocal Rank Fusion:**
```
For each document appearing in either list:
  rrf_score += 1 / (k + rank_in_dense + 1)    if in dense results
  rrf_score += 1 / (k + rank_in_sparse + 1)   if in sparse results

Where k = 60 (dampening constant)
```

This ensures a document ranked #1 in both lists gets: `1/61 + 1/61 = 0.0328`
A document ranked #1 in one list only gets: `1/61 = 0.0164`

The top-4 documents by RRF score become the context for LLM generation.

---

## 4. Guardrail Decision Flow

```
                    ┌──────────────────────┐
                    │    User Query        │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  INPUT GUARDRAIL     │
                    │                      │
                    │  ① Injection check   │──YES──▶ Return security warning
                    │    (regex patterns)  │
                    │                      │
                    │  ② Domain check      │──YES──▶ Return domain warning
                    │    (financial keywords)│        (only if query > 3 words
                    │                      │         with no financial terms)
                    └──────────┬───────────┘
                               │ PASS
                    ┌──────────▼───────────┐
                    │  RETRIEVAL           │
                    │  Dense + Sparse + RRF│
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  CONTEXT GATE        │
                    │                      │
                    │  RRF score < 0.01?   │──YES──▶ Return "insufficient context"
                    │                      │         (LLM call SKIPPED = $0 cost)
                    └──────────┬───────────┘
                               │ PASS (score ≥ 0.01)
                    ┌──────────▼───────────┐
                    │  LLM GENERATION      │
                    │  (Bedrock Claude)    │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  OUTPUT GUARDRAIL    │
                    │                      │
                    │  ① Extract numbers   │
                    │    from response     │
                    │                      │
                    │  ② Check each number │
                    │    exists in context │
                    │                      │
                    │  ③ If not found:     │──▶ Append [!WARNING] notice
                    │    flag hallucination│
                    │                      │
                    │  ④ Always append     │──▶ Financial disclaimer
                    │    disclaimer        │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  RETURN RESPONSE     │
                    │  + Citations         │
                    └──────────────────────┘
```

---

## 5. Spot Instance Interruption Flow

When AWS reclaims the RAG App Spot instance:

```
Timeline:
─────────────────────────────────────────────────────────────────────▶

T+0s: AWS sends 2-minute interruption notice
      Instance receives SIGTERM
      Docker container stops gracefully

T+2m: Instance stopped
      ALB health check starts failing

T+2m30s: ALB detects 1st failure (unhealthy count: 1/3)
T+3m00s: ALB detects 2nd failure (unhealthy count: 2/3)
T+3m30s: ALB detects 3rd failure → Target marked UNHEALTHY
         ALB stops routing traffic

T+3m30s-T+5m: Spot fleet requests replacement instance
               New instance launches in private subnet

T+5m-T+10m: user_data/rag_app.sh executes:
            - Install Docker (~1 min)
            - Clone repo + build image (~3 min)
            - Start container (~30s)
            - Health check passes

T+10m: ALB detects 1st healthy response (healthy count: 1/2)
T+10m30s: ALB detects 2nd healthy response → Target HEALTHY
          Traffic resumes
```

**User impact:** ~7-8 minutes of downtime. During this window, users see ALB 502/503 errors.

**Data impact:** Zero. RuVector runs on a separate On-Demand instance. All vector data is preserved.

---

## 6. Error Handling Flows

### 6.1 Bedrock Timeout/Throttle

```
RAG App                        Bedrock
   │                              │
   │──InvokeModel────────────────▶│
   │                              │──[30s timeout exceeded]
   │◀─TimeoutError────────────────│
   │                              │
   │──[Retry 1, exponential backoff]──▶│
   │◀─ThrottlingException─────────│
   │                              │
   │──[Retry 2, longer backoff]───▶│
   │◀─200 OK─────────────────────│
   │                              │
```

**Config:** Max 3 retries with adaptive backoff (botocore adaptive mode).
**Fallback:** If all retries fail, return error message to user. Service continues accepting requests.

### 6.2 RuVector Connection Failure

```
RAG App                        RuVector
   │                              │
   │──POST /points/search─────────▶│ (connection refused / timeout)
   │◀─ConnectionError─────────────│
   │                              │
   │  dense_results = []          │
   │  Continue with sparse-only   │
   │  retrieval if BM25 available │
```

The system degrades gracefully:
- Dense search fails → sparse-only results used
- Both fail → "No documents ingested" message returned
- Health endpoint reports `"ruvector": "unavailable"`

### 6.3 Document Parse Failure

```
User                    RAG App
 │                        │
 │──POST /ingest─────────▶│
 │  (corrupted.pdf)      │
 │                        │──[PdfReader throws]
 │                        │
 │◀──400 Bad Request──────│
 │  {"detail": "Failed   │
 │   to parse document:  │
 │   [error details]"}   │
 │                        │
 │  Job status:           │
 │  "failed"              │
 │  Temp file: deleted    │
```

### 6.4 Upload Size Exceeded

```
User                    RAG App
 │                        │
 │──POST /ingest─────────▶│
 │  (150MB file)          │
 │                        │──[Streaming, counting bytes]
 │                        │  At 100MB+1 byte:
 │                        │──[Delete temp file]
 │                        │
 │◀──413 Payload Too Large│
 │  {"detail": "File     │
 │   exceeds 100 MB"}    │
```

---

## 7. Session Persistence Flow

The frontend persists chat history to survive page reloads.

```
┌─────────────────── Page Lifecycle ───────────────────────┐
│                                                           │
│  Page Load                                                │
│  ├── DOMContentLoaded fires                              │
│  ├── restoreSession()                                     │
│  │   ├── Read localStorage['rag_chat_session']           │
│  │   ├── Parse JSON → messages array                     │
│  │   ├── For each message:                               │
│  │   │   ├── type='user'   → appendMessage(text, 'user')│
│  │   │   ├── type='bot'    → appendMessage + citations   │
│  │   │   └── type='system' → addSystemMessage(text)      │
│  │   └── Hide welcome screen if messages exist           │
│  └── Start health check interval                         │
│                                                           │
│  User Interaction                                         │
│  ├── Send message                                         │
│  │   ├── appendMessage(text, 'user') → saves to DOM      │
│  │   ├── saveSession() → serialize DOM → localStorage    │
│  │   ├── Fetch /chat                                      │
│  │   ├── updateBotMessage(id, response, citations)        │
│  │   └── saveSession() → update localStorage             │
│  │                                                        │
│  ├── Upload document                                      │
│  │   ├── Fetch /ingest                                    │
│  │   ├── addSystemMessage(success) → saves to DOM         │
│  │   └── saveSession() → update localStorage             │
│  │                                                        │
│  └── New Chat clicked                                     │
│      ├── confirm() dialog                                 │
│      ├── clearSession() → localStorage.removeItem()       │
│      └── location.reload() → fresh page                  │
│                                                           │
└───────────────────────────────────────────────────────────┘
```

### Storage Schema

```json
{
  "messages": [
    {
      "type": "user",
      "text": "What was EBITDA in Q3 2024?",
      "citations": [],
      "timestamp": 1700000000000
    },
    {
      "type": "bot",
      "text": "Based on the annual report, Q3 2024 EBITDA was...",
      "citations": [
        {
          "source": "annual_report_2024.pdf",
          "type": "table_row",
          "header": "Quarterly EBITDA",
          "snippet": "Q3 2024: $142.5M..."
        }
      ],
      "timestamp": 1700000002000
    },
    {
      "type": "system",
      "text": "Successfully ingested document **report.pdf** (42 chunks loaded)...",
      "citations": [],
      "timestamp": 1700000005000
    }
  ],
  "created": 1700000000000
}
```

**Storage limits:** localStorage typically allows 5-10 MB. For a chat session with ~100 messages and citations, usage is typically under 500 KB.

---

## 8. End-to-End Latency Breakdown

Typical chat query latency (warm system, documents already ingested):

| Step | Duration | Notes |
|------|----------|-------|
| Input guardrail | < 1 ms | Regex matching, in-memory |
| Query embedding (Bedrock Titan) | 100-300 ms | VPC Endpoint, no internet hop |
| Dense search (RuVector) | 5-20 ms | Same AZ, private subnet |
| BM25 sparse search | 1-5 ms | In-memory, local process |
| RRF fusion | < 1 ms | Simple arithmetic |
| Context gate check | < 1 ms | Score comparison |
| LLM generation (Bedrock Haiku) | 800-2000 ms | Depends on output length |
| Output guardrail | 1-5 ms | Regex + string matching |
| **Total** | **~1-2.5 seconds** | |

The context gate (step 6) can short-circuit the entire LLM call, reducing latency to ~200-400 ms for low-confidence queries while also saving ~$0.003 per skipped call.

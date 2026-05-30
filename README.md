# RuVector RAG ChatBot — AWS Multi-Service Architecture

A production-grade Retrieval-Augmented Generation (RAG) chatbot for corporate financial document analysis. Built on a multi-service AWS architecture with RuVector as the vector database, AWS Bedrock for AI (Titan embeddings + Claude 3.5 Haiku), and a FastAPI backend with hybrid dense+sparse retrieval.

## Features

- **Hybrid Retrieval** — Dense vector search (RuVector HNSW) + Sparse keyword search (BM25) blended via Reciprocal Rank Fusion
- **AWS Bedrock Integration** — Titan Embed Text v2 (1024-dim) for embeddings, Claude 3.5 Haiku for generation
- **Multi-Service Architecture** — RAG App and RuVector DB on separate EC2 instances connected via private VPC
- **Financial Guardrails** — Input validation, domain filtering, context score gating, output hallucination detection
- **Large Document Support** — Stream-based ingestion up to 100 MB with progress tracking
- **Session Persistence** — Chat history survives page reloads via localStorage
- **Cost-Optimized** — Spot instances, VPC endpoints (no NAT Gateway), context-gated LLM calls, target < $50/month
- **Glassmorphic UI** — Premium dark-mode chat interface with citation exploration

## Quick Start

### Local Development (Docker Compose)

```bash
# Clone the repo
git clone https://github.com/Kedar-Gaikwad/Ruvector_Rag_ChatBot.git
cd Ruvector_Rag_ChatBot

# Start both services locally
docker compose up --build

# Access at http://localhost
```

### AWS Deployment (Terraform)

```bash
cd aws
terraform init
terraform plan -var="budget_alert_email=you@email.com"
terraform apply -var="budget_alert_email=you@email.com"

# Output: ALB DNS name → http://<alb-dns>
```

## Project Structure

```
Ruvector_Rag_ChatBot/
├── aws/                        # Terraform IaC (multi-service)
│   ├── main.tf                 # VPC, subnets, route tables
│   ├── security_groups.tf      # ALB, RAG App, RuVector, VPCE SGs
│   ├── iam.tf                  # Roles: Bedrock, CloudWatch, ECR, SSM
│   ├── compute.tf              # EC2 Spot (RAG) + On-Demand (RuVector)
│   ├── alb.tf                  # Application Load Balancer + health checks
│   ├── vpc_endpoints.tf        # Bedrock, ECR, CloudWatch, S3, SSM
│   ├── storage.tf              # 40 GB gp3 EBS for vector persistence
│   ├── monitoring.tf           # CloudWatch Log Groups + $50 Budget alarm
│   ├── variables.tf            # Configurable inputs
│   ├── outputs.tf              # ALB DNS, instance IDs
│   └── user_data/
│       ├── rag_app.sh          # Bootstrap: Docker + RAG container
│       └── ruvector.sh         # Bootstrap: Docker + EBS mount + RuVector
├── rag_app/
│   ├── backend/
│   │   ├── app.py              # FastAPI: /health, /ingest, /chat, /collections
│   │   ├── chunker.py          # SmartFinancialChunker
│   │   ├── bm25.py             # Local sparse BM25 index
│   │   └── requirements.txt    # Python dependencies
│   ├── frontend/
│   │   └── index.html          # Glassmorphic chat UI with session persistence
│   ├── Dockerfile              # Container image for RAG App
│   └── launcher/               # Rust launcher (legacy, unused in multi-service)
├── docs/                       # In-depth documentation
│   ├── README.md               # Technical overview
│   ├── ARCHITECTURE.md         # AWS architecture deep-dive
│   └── PROCESS_FLOW.md         # End-to-end request/ingestion flows
├── docker-compose.yml          # Local dev (mirrors prod architecture)
└── .kiro/specs/                # Requirements & design specs
```

## Documentation

| Document | Description |
|----------|-------------|
| [docs/README.md](docs/README.md) | Technical overview and component descriptions |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | AWS infrastructure design, networking, cost breakdown |
| [docs/PROCESS_FLOW.md](docs/PROCESS_FLOW.md) | Request lifecycle, ingestion pipeline, error handling |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Vector DB | RuVector (Rust HNSW, Docker) |
| Backend | Python 3.11, FastAPI, Uvicorn |
| Embeddings | AWS Bedrock Titan Embed Text v2 (1024-dim) |
| LLM | AWS Bedrock Claude 3.5 Haiku |
| Frontend | Vanilla JS, Glassmorphic CSS |
| Infrastructure | Terraform, EC2, ALB, VPC, EBS |
| Observability | CloudWatch Logs (7-day retention) |
| Container | Docker, Docker Compose |

## License

MIT

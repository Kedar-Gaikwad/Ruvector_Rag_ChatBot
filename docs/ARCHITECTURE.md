# AWS Architecture — RuVector RAG ChatBot

## Overview

Two-instance AWS architecture deployed via a single `terraform apply`. A stateless FastAPI RAG application runs on EC2 Spot, and a persistent Qdrant vector database runs on EC2 On-Demand. Both instances are in the public subnet with internet access for bootstrapping. AWS Bedrock provides embeddings and LLM. Amazon Textract provides OCR for scanned documents.

**Design Goals:**
- Service isolation (independent scaling, updates, restarts)
- Public subnet deployment for internet access (git clone, docker pull) without NAT Gateway
- Managed AI via Bedrock (no GPU instances)
- OCR for scanned PDFs via Textract (no self-hosted OCR)
- Cost target under $50/month for POC
- Single `terraform apply` deployment

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS Cloud (us-east-1)                          │
│                                                                             │
│  ┌───────────────────────── VPC 10.0.0.0/16 ─────────────────────────────┐ │
│  │                                                                        │ │
│  │  ┌──── Public Subnet A 10.0.1.0/24 ──┐  ┌── Public Subnet B ──┐      │ │
│  │  │                                    │  │    10.0.3.0/24      │      │ │
│  │  │   ┌──────────────────────────┐     │  │    (ALB AZ-b)       │      │ │
│  │  │   │   Application Load       │     │  └─────────────────────┘      │ │
│  │  │   │   Balancer (ALB)         │     │                               │ │
│  │  │   │   Port 80 → :8000        │     │                               │ │
│  │  │   │   idle_timeout = 300s    │     │                               │ │
│  │  │   └──────────┬───────────────┘     │                               │ │
│  │  │              │ :8000               │                               │ │
│  │  │   ┌──────────▼───────────────┐    ┌─────────────────────────┐     │ │
│  │  │   │   RAG App EC2 (Spot)     │    │   Qdrant EC2 (On-Demand)│     │ │
│  │  │   │   t3.medium              │    │   t3.medium             │     │ │
│  │  │   │   --network host         │    │                         │     │ │
│  │  │   │                          │    │   ┌─────────────────┐   │     │ │
│  │  │   │   ┌──────────────────┐   │    │   │ qdrant:v1.9.2   │   │     │ │
│  │  │   │   │ rag-chatbot      │───┼────┼──▶│ Port 6333 REST  │   │     │ │
│  │  │   │   │ FastAPI :8000    │   │    │   │ Port 6334 gRPC  │   │     │ │
│  │  │   │   └──────────────────┘   │    │   └────────┬────────┘   │     │ │
│  │  │   │                          │    │            │            │     │ │
│  │  │   │   Root: 20 GB gp3        │    │   Root: 20 GB gp3      │     │ │
│  │  │   └──────────────────────────┘    │            │            │     │ │
│  │  │                                    │   ┌────────▼────────┐  │     │ │
│  │  │                                    │   │ EBS 40 GB gp3   │  │     │ │
│  │  │                                    │   │ /var/lib/qdrant/ │  │     │ │
│  │  │                                    │   │ storage          │  │     │ │
│  │  │                                    │   └─────────────────┘  │     │ │
│  │  │                                    └─────────────────────────┘     │ │
│  │  │                                                                     │ │
│  │  │   ┌─────────────────── VPC Endpoints (public subnet) ───────────┐  │ │
│  │  │   │  • bedrock-runtime (Interface) — Titan + Claude calls       │  │ │
│  │  │   │  • ecr.api + ecr.dkr (Interface) — Docker image pulls       │  │ │
│  │  │   │  • s3 (Gateway, free) — ECR layer storage                   │  │ │
│  │  │   │  • ssm + ssmmessages + ec2messages (Interface) — SSM access │  │ │
│  │  │   └─────────────────────────────────────────────────────────────┘  │ │
│  │  └─────────────────────────────────────────────────────────────────────┘ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  ┌─── AWS Managed Services ──────────────────────────────────────────────┐  │
│  │  • AWS Bedrock Titan Embed Text v2 (1024-dim embeddings)              │  │
│  │  • AWS Bedrock Claude Haiku 4.5 (US Cross-Region Inference Profile)   │  │
│  │  • Amazon Textract (OCR + form key-value extraction for scanned PDFs) │  │
│  │  • AWS Budgets ($50/month alarm at 80% forecast + 100% actual)        │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Network Design

### Subnets

| Subnet | CIDR | Type | AZ | Purpose |
|--------|------|------|----|---------|
| Public A | 10.0.1.0/24 | Public | us-east-1a | EC2 instances + VPC Endpoints |
| Public B | 10.0.3.0/24 | Public | us-east-1b | ALB multi-AZ requirement |
| Private | 10.0.2.0/24 | Private | us-east-1a | Reserved (unused) |

**Both EC2 instances are in the public subnet** with public IPs and internet access via the Internet Gateway. This is required for `git clone` and `docker pull` during bootstrap. VPC endpoints are also in the public subnet so instances can reach AWS services via the AWS backbone.

### Security Groups

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   ALB SG        │     │  RAG App SG     │     │  Qdrant SG      │
│                 │     │                 │     │                 │
│ IN:  80/tcp     │────▶│ IN:  8000/tcp   │────▶│ IN:  6333/tcp   │
│      0.0.0.0/0  │     │      from ALB SG│     │      from RAG SG│
│                 │     │      22/tcp     │     │      6333/tcp   │
│ OUT: all        │     │      admin CIDR │     │      admin CIDR │
│                 │     │                 │     │      22/tcp     │
│                 │     │ OUT: all        │     │      admin CIDR │
└─────────────────┘     └─────────────────┘     │                 │
                                                 │ OUT: all        │
                                                 └─────────────────┘
```

Port 6333 is open to `admin_ip_cidr` on the Qdrant SG for direct debugging (`curl http://<qdrant-ip>:6333/healthz`).

---

## Compute Architecture

### RAG App Instance (Spot)

| Property | Value |
|----------|-------|
| Instance Type | t3.medium (2 vCPU, 4 GB RAM) |
| Pricing Model | Spot (persistent, stop on interruption) |
| AMI | Ubuntu 22.04 amd64 |
| Subnet | Public (10.0.1.0/24) |
| Public IP | Yes (for bootstrap internet access) |
| Root Volume | 20 GB gp3 |
| Docker Network | `--network host` (required for IMDS IAM credential access) |
| IAM Role | Bedrock + Textract + ECR + SSM |
| Container | `rag-chatbot` (built from Dockerfile at boot) |
| Exposed Port | 8000 (to ALB SG only) |

**Why `--network host`?** The container needs to reach `169.254.169.254` (EC2 Instance Metadata Service) to get IAM role credentials for Bedrock and Textract. Bridge networking blocks this address.

### Qdrant Instance (On-Demand)

| Property | Value |
|----------|-------|
| Instance Type | t3.medium (2 vCPU, 4 GB RAM) |
| Pricing Model | On-Demand (protected from interruption) |
| AMI | Ubuntu 22.04 amd64 |
| Subnet | Public (10.0.1.0/24) |
| Public IP | Yes (for docker pull qdrant/qdrant) |
| Root Volume | 20 GB gp3 |
| Data Volume | 40 GB gp3 (separate EBS, mounted at /var/lib/qdrant/storage) |
| IAM Role | ECR + SSM |
| Container | `qdrant/qdrant:v1.9.2` |
| Exposed Ports | 6333 REST, 6334 gRPC |

**Why On-Demand?** Qdrant holds persistent vector data. Spot interruptions risk data corruption during write operations.

---

## VPC Endpoints

Interface endpoints keep AWS API calls inside the AWS network (lower latency, no internet data transfer charges). All endpoints are in the public subnet to match the EC2 instances.

| Endpoint | Service | Type | Purpose |
|----------|---------|------|---------|
| bedrock-runtime | `bedrock-runtime` | Interface | Titan embeddings + Claude LLM |
| ecr.api | `ecr.api` | Interface | Docker image metadata |
| ecr.dkr | `ecr.dkr` | Interface | Docker image layer pulls |
| s3 | `s3` | Gateway (free) | ECR image layer storage |
| ssm | `ssm` | Interface | Systems Manager core |
| ssmmessages | `ssmmessages` | Interface | Session Manager shell |
| ec2messages | `ec2messages` | Interface | SSM agent communication |

**`depends_on`** is set on all Interface endpoints pointing to the VPC endpoint SG. This ensures Terraform destroys endpoints before the SG on `terraform destroy`, preventing `DependencyViolation` errors.

---

## Load Balancer

| Property | Value |
|----------|-------|
| Type | Application Load Balancer (Layer 7) |
| Scheme | Internet-facing |
| Subnets | Public A (10.0.1.0/24) + Public B (10.0.3.0/24) |
| Idle Timeout | **300 seconds** (raised from default 60s to handle large document ingestion) |
| Listener | HTTP:80 → Forward to RAG App TG |
| Target Group | RAG App on port 8000 |
| Health Check | GET /health every 30s, timeout 5s |
| Healthy Threshold | 2 consecutive successes |
| Unhealthy Threshold | 3 consecutive failures |

The 300s idle timeout is required because document ingestion (PDF parsing + Bedrock embedding calls) can take several minutes for large files. The frontend uses async polling (`/ingest/status/{job_id}`) so the HTTP connection is released immediately after upload.

---

## Storage

| Volume | Attached To | Size | Type | IOPS | Delete on Termination |
|--------|-------------|------|------|------|-----------------------|
| RAG App Root | RAG App EC2 | 20 GB | gp3 | default | Yes |
| Qdrant Root | Qdrant EC2 | 20 GB | gp3 | default | Yes |
| Qdrant Data | Qdrant EC2 | 40 GB | gp3 | 3000 | **No** |

The Qdrant data volume is mounted at `/var/lib/qdrant/storage` inside the container. The `ruvector.sh` bootstrap script detects the device name (xvdf, nvme1n1, or sdf), formats it with ext4 on first use, and persists the mount in `/etc/fstab`.

---

## IAM Architecture

### RAG App Role (`ruvector-rag-app-role`)

```
ruvector-rag-app-role
├── AmazonSSMManagedInstanceCore        (Session Manager shell access)
├── ruvector-rag-bedrock-policy         (bedrock:InvokeModel, bedrock:ListFoundationModels)
├── ruvector-rag-textract-policy        (textract:DetectDocumentText, textract:AnalyzeDocument)
└── ruvector-rag-ecr-policy             (ecr:GetAuthorizationToken, ecr:BatchGetImage, ...)
```

### Qdrant Role (`ruvector-ruvector-role`)

```
ruvector-ruvector-role
├── AmazonSSMManagedInstanceCore        (Session Manager shell access)
└── ruvector-rag-ecr-policy             (ecr:GetAuthorizationToken, ecr:BatchGetImage, ...)
```

---

## Deployment Sequence

```
terraform apply
    │
    ├─1─▶ VPC + Subnets + IGW + Route Tables
    ├─2─▶ Security Groups
    ├─3─▶ IAM Roles + Policies + Instance Profiles
    ├─4─▶ VPC Endpoints (depends_on SGs)
    ├─5─▶ EBS Volume (Qdrant data, 40 GB gp3)
    ├─6─▶ Qdrant EC2 (On-Demand)
    │        └── user_data/ruvector.sh:
    │            ├── Install Docker
    │            ├── Detect + mount EBS (/var/lib/qdrant/storage)
    │            └── docker run qdrant/qdrant:v1.9.2
    ├─7─▶ RAG App EC2 (Spot Request)
    │        └── user_data/rag_app.sh:
    │            ├── Install Docker
    │            ├── Wait for Qdrant /healthz (up to 5 min)
    │            ├── git clone Kedar-Gaikwad/rag_app
    │            ├── docker build rag-chatbot
    │            ├── docker run --network host
    │            └── Health check + status report
    ├─8─▶ ALB + Target Group + Listener
    └─9─▶ Budget Alarm ($50/month)
```

**Total provisioning time:** ~8-12 minutes

---

## Cost Breakdown (Estimated Monthly)

| Resource | Pricing | Estimated Cost |
|----------|---------|----------------|
| RAG App EC2 (t3.medium Spot) | ~$0.013/hr × 730hr | ~$9.50 |
| Qdrant EC2 (t3.medium On-Demand) | ~$0.0416/hr × 730hr | ~$30.37 |
| EBS Volumes (80 GB gp3 total) | $0.08/GB/month | ~$6.40 |
| ALB | $0.0225/hr + LCU | ~$16.43 |
| VPC Endpoints (7 Interface) | $0.01/hr each | ~$50.40 |
| Bedrock (Titan + Claude Haiku) | Per-token | ~$2-5 |
| Textract | $1.50/1000 pages | ~$0-2 |
| **Total** | | **~$50-70** |

---

## Outputs After `terraform apply`

| Output | Description |
|--------|-------------|
| `alb_dns_name` | RAG Chatbot URL: `http://<value>` |
| `rag_app_instance_id` | Spot instance ID |
| `rag_app_public_ip` | Public IP for SSH |
| `ruvector_instance_id` | Qdrant instance ID |
| `ruvector_public_ip` | Public IP for SSH |
| `ruvector_private_ip` | Private IP used by RAG App → Qdrant |
| `ssh_rag_app` | Ready-to-use SSH command |
| `ssh_ruvector` | Ready-to-use SSH command |
| `qdrant_health_check` | `curl` command to verify Qdrant |

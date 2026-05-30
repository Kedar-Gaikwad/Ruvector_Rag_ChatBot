# AWS Architecture — RuVector RAG ChatBot

## Overview

This document describes the complete AWS infrastructure for the RuVector RAG ChatBot multi-service deployment. The architecture separates the stateless RAG application from the persistent vector database onto independent EC2 instances, connected via private VPC networking, with AWS Bedrock for AI and an Application Load Balancer for external access.

**Design Goals:**
- Service isolation (independent scaling, updates, restarts)
- Private network communication (no public exposure of vector DB)
- Managed AI via Bedrock (no GPU instances)
- Cost target under $50/month for POC
- Single `terraform apply` deployment

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS Cloud (us-east-1)                           │
│                                                                             │
│  ┌───────────────────────── VPC 10.0.0.0/16 ─────────────────────────────┐ │
│  │                                                                        │ │
│  │  ┌──── Public Subnet 10.0.1.0/24 ────┐  ┌── Public Subnet B ──┐      │ │
│  │  │                                    │  │    10.0.3.0/24      │      │ │
│  │  │   ┌──────────────────────────┐     │  │    (ALB AZ-b)      │      │ │
│  │  │   │   Application Load       │     │  └────────────────────┘      │ │
│  │  │   │   Balancer (ALB)         │     │                              │ │
│  │  │   │   Port 80 (HTTP)         │     │                              │ │
│  │  │   └──────────┬───────────────┘     │                              │ │
│  │  └──────────────┼────────────────────-┘                              │ │
│  │                 │ :8000                                               │ │
│  │  ┌──── Private Subnet 10.0.2.0/24 ───────────────────────────────┐   │ │
│  │  │              │                                                  │   │ │
│  │  │   ┌──────────▼───────────────┐    ┌─────────────────────────┐ │   │ │
│  │  │   │   RAG App EC2            │    │   RuVector EC2          │ │   │ │
│  │  │   │   t3.medium (Spot)       │    │   t3.medium (On-Demand) │ │   │ │
│  │  │   │                          │    │                         │ │   │ │
│  │  │   │   ┌──────────────────┐   │    │   ┌─────────────────┐  │ │   │ │
│  │  │   │   │ Docker Container │   │    │   │ Docker Container│  │ │   │ │
│  │  │   │   │ rag-chatbot      │   │    │   │ ruvector:latest │  │ │   │ │
│  │  │   │   │ Port 8000        │───┼────┼──▶│ Port 6333       │  │ │   │ │
│  │  │   │   └──────────────────┘   │    │   └─────────────────┘  │ │   │ │
│  │  │   │                          │    │           │             │ │   │ │
│  │  │   │   Root: 20 GB gp3       │    │   Root: 20 GB gp3     │ │   │ │
│  │  │   └──────────────────────────┘    │           │             │ │   │ │
│  │  │                                    │   ┌───────▼─────────┐  │ │   │ │
│  │  │                                    │   │ EBS Data Volume │  │ │   │ │
│  │  │                                    │   │ 40 GB gp3       │  │ │   │ │
│  │  │                                    │   │ /var/lib/        │  │ │   │ │
│  │  │                                    │   │  ruvector/data   │  │ │   │ │
│  │  │                                    │   └─────────────────┘  │ │   │ │
│  │  │                                    └─────────────────────────┘ │   │ │
│  │  │                                                                │   │ │
│  │  │   ┌─────────────────── VPC Endpoints ──────────────────────┐   │   │ │
│  │  │   │  • Bedrock Runtime (Interface)                         │   │   │ │
│  │  │   │  • ECR API + ECR DKR (Interface)                       │   │   │ │
│  │  │   │  • CloudWatch Logs (Interface)                         │   │   │ │
│  │  │   │  • S3 (Gateway)                                        │   │   │ │
│  │  │   │  • SSM + SSM Messages + EC2 Messages (Interface)       │   │   │ │
│  │  │   └────────────────────────────────────────────────────────┘   │   │ │
│  │  └────────────────────────────────────────────────────────────────┘   │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  ┌─── AWS Managed Services ──────────────────────────────────────────────┐  │
│  │  • AWS Bedrock (Titan Embed v2 + Claude 3.5 Haiku)                    │  │
│  │  • Amazon ECR (RAG App Docker image)                                  │  │
│  │  • CloudWatch Logs (7-day retention, structured logs)                 │  │
│  │  • AWS Budgets ($50/month alarm)                                      │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Network Design

### Subnets

| Subnet | CIDR | Type | AZ | Purpose |
|--------|------|------|------|---------|
| Public A | 10.0.1.0/24 | Public | us-east-1a | ALB placement |
| Public B | 10.0.3.0/24 | Public | us-east-1b | ALB multi-AZ requirement |
| Private | 10.0.2.0/24 | Private | us-east-1a | EC2 instances + VPC Endpoints |

### Route Tables

| Route Table | Destination | Target | Purpose |
|-------------|-------------|--------|---------|
| Public RT | 0.0.0.0/0 | Internet Gateway | ALB internet access |
| Private RT | 10.0.0.0/16 | local | VPC internal routing |
| Private RT | S3 prefix list | S3 Gateway Endpoint | ECR image layer pulls |

**No NAT Gateway** — All AWS service access from the private subnet goes through VPC Interface Endpoints. This saves ~$32/month.

### Security Groups

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   ALB SG        │     │  RAG App SG     │     │  RuVector SG    │
│                 │     │                 │     │                 │
│ IN:  80/tcp     │────▶│ IN:  8000/tcp   │────▶│ IN:  6333/tcp   │
│      from 0.0.0.0│     │      from ALB SG│     │      from RAG SG│
│                 │     │      22/tcp     │     │      22/tcp     │
│ OUT: all        │     │      from admin │     │      from admin │
│                 │     │                 │     │                 │
│                 │     │ OUT: all        │     │ OUT: all        │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

**Traffic isolation:** RuVector only accepts connections from the RAG App security group on port 6333. No public IP, no internet route. The vector database is completely private.

---

## Compute Architecture

### RAG App Instance (Spot)

| Property | Value |
|----------|-------|
| Instance Type | t3.medium (2 vCPU, 4 GB RAM) |
| Pricing Model | Spot (persistent, stop on interruption) |
| AMI | Ubuntu 22.04 amd64 |
| Root Volume | 20 GB gp3 |
| Subnet | Private (10.0.2.0/24) |
| IAM Role | Bedrock + CloudWatch + ECR + SSM |
| Container | `rag-chatbot` (built from Dockerfile) |
| Exposed Port | 8000 (to ALB only) |

**Why Spot?** The RAG App is stateless. If a Spot interruption occurs, the instance stops and the ALB routes traffic away. When a new Spot instance launches, it bootstraps automatically via user_data and re-registers with the target group.

### RuVector Instance (On-Demand)

| Property | Value |
|----------|-------|
| Instance Type | t3.medium (2 vCPU, 4 GB RAM) |
| Pricing Model | On-Demand (protected from interruption) |
| AMI | Ubuntu 22.04 amd64 |
| Root Volume | 20 GB gp3 |
| Data Volume | 40 GB gp3 (separate EBS, `delete_on_termination = false`) |
| Subnet | Private (10.0.2.0/24) |
| IAM Role | CloudWatch + SSM |
| Container | `ruvnet/ruvector:latest` |
| Exposed Port | 6333 (to RAG App SG only) |

**Why On-Demand?** RuVector holds persistent vector data. Spot interruptions would risk data corruption during write operations.

---

## VPC Endpoints (NAT Gateway Replacement)

Instead of a $32+/month NAT Gateway, we use VPC Interface Endpoints for private AWS service access:

| Endpoint | Service | Type | Purpose |
|----------|---------|------|---------|
| Bedrock Runtime | `bedrock-runtime` | Interface | Embedding + LLM API calls |
| ECR API | `ecr.api` | Interface | Docker image metadata |
| ECR DKR | `ecr.dkr` | Interface | Docker image layer pulls |
| CloudWatch Logs | `logs` | Interface | Structured log delivery |
| S3 | `s3` | Gateway (free) | ECR image layer storage |
| SSM | `ssm` | Interface | Systems Manager access |
| SSM Messages | `ssmmessages` | Interface | Session Manager connections |
| EC2 Messages | `ec2messages` | Interface | Instance metadata |

**Cost:** Interface endpoints cost ~$0.01/hour each + data transfer. For a low-traffic POC, this is significantly cheaper than NAT Gateway ($0.045/hour + $0.045/GB).

---

## Load Balancer Configuration

| Property | Value |
|----------|-------|
| Type | Application Load Balancer (Layer 7) |
| Scheme | Internet-facing |
| Subnets | Public A + Public B (multi-AZ required) |
| Listener | HTTP:80 → Forward to RAG App TG |
| Target Group | RAG App on port 8000 |
| Health Check | GET /health every 30s, timeout 5s |
| Healthy Threshold | 2 consecutive successes |
| Unhealthy Threshold | 3 consecutive failures |

**Failover behavior:** If the RAG App Spot instance is interrupted, the ALB detects unhealthy status within 90 seconds (3 × 30s interval) and stops routing traffic. When the replacement Spot instance bootstraps and passes 2 health checks, traffic resumes.

---

## Storage Architecture

### EBS Volume Strategy

| Volume | Attached To | Size | Type | Delete on Termination |
|--------|-------------|------|------|-----------------------|
| RAG App Root | RAG App EC2 | 20 GB | gp3 | Yes |
| RuVector Root | RuVector EC2 | 20 GB | gp3 | Yes |
| RuVector Data | RuVector EC2 | 40 GB | gp3 | **No** |

The RuVector data volume is protected:
- `delete_on_termination = false` — survives instance termination
- Mounted at `/var/lib/ruvector/data` inside the container
- Formatted as ext4 on first use, preserved on subsequent boots
- Contains all vector collections and HNSW index data

---

## IAM Architecture

### RAG App Role Permissions

```
ruvector-rag-app-role
├── AmazonSSMManagedInstanceCore     (Systems Manager access)
├── ruvector-rag-bedrock-policy      (bedrock:InvokeModel, bedrock:ListFoundationModels)
├── ruvector-rag-cloudwatch-policy   (logs:CreateLogStream, logs:PutLogEvents, ...)
└── ruvector-rag-ecr-policy          (ecr:GetAuthorizationToken, ecr:BatchGetImage, ...)
```

### RuVector Role Permissions

```
ruvector-ruvector-role
├── AmazonSSMManagedInstanceCore     (Systems Manager access)
└── ruvector-rag-cloudwatch-policy   (logs:CreateLogStream, logs:PutLogEvents, ...)
```

**Principle of least privilege:** RuVector has no Bedrock or ECR access — it only needs CloudWatch for logging and SSM for remote management.

---

## Observability

### CloudWatch Log Groups

| Log Group | Source | Retention |
|-----------|--------|-----------|
| `/ruvector-rag/rag-app` | RAG App application logs + user-data bootstrap | 7 days |
| `/ruvector-rag/ruvector` | RuVector container stdout/stderr + user-data | 7 days |

### Log Format (CloudWatch Insights Compatible)

```
2024-11-15T14:32:01 service=rag-app level=INFO request_id=a1b2c3d4 Query: 'What was Q3 revenue?' collection=finance_docs
2024-11-15T14:32:02 service=rag-app level=WARNING request_id=a1b2c3d4 Context gate: RRF score 0.008 below threshold, skipping LLM
```

**Queryable fields:** service, level, request_id, timestamp

### CloudWatch Insights Example Queries

```sql
-- Find all errors in the last hour
fields @timestamp, @message
| filter level = "ERROR"
| sort @timestamp desc
| limit 50

-- Track Bedrock call latency
fields @timestamp, @message
| filter @message like /elapsed_ms/
| stats avg(elapsed_ms) by bin(5m)

-- Find blocked queries
fields @timestamp, @message
| filter @message like /guardrail blocked/
| sort @timestamp desc
```

---

## Cost Breakdown (Estimated Monthly)

| Resource | Pricing | Estimated Cost |
|----------|---------|----------------|
| RAG App EC2 (t3.medium Spot) | ~$0.013/hr × 730hr | ~$9.50 |
| RuVector EC2 (t3.medium On-Demand) | ~$0.0416/hr × 730hr | ~$30.37 |
| EBS Volumes (80 GB gp3 total) | $0.08/GB/month | ~$6.40 |
| ALB | $0.0225/hr + LCU | ~$16.43 |
| VPC Endpoints (7 Interface) | $0.01/hr each | ~$50.40 |
| CloudWatch Logs | $0.50/GB ingested | ~$0.50 |
| Bedrock (Titan + Haiku) | Per-token | ~$2-5 |
| **Total (no traffic)** | | **~$50-65** |

**Cost optimization levers:**
- Reduce VPC endpoints (remove SSM endpoints if not needed): saves ~$21/month
- Use a single AZ for non-HA POC
- Context gate prevents unnecessary Bedrock LLM calls (each saved call = ~$0.003)

---

## Deployment Sequence

```
terraform apply
    │
    ├─1─▶ VPC + Subnets + IGW + Route Tables
    ├─2─▶ Security Groups
    ├─3─▶ IAM Roles + Instance Profiles
    ├─4─▶ VPC Endpoints
    ├─5─▶ CloudWatch Log Groups
    ├─6─▶ EBS Volume (RuVector data)
    ├─7─▶ RuVector EC2 (On-Demand)
    │        └── user_data/ruvector.sh executes:
    │            ├── Install Docker
    │            ├── Wait for EBS attach + mount
    │            ├── Install CloudWatch Agent
    │            └── docker run ruvnet/ruvector:latest
    ├─8─▶ RAG App EC2 (Spot Request)
    │        └── user_data/rag_app.sh executes:
    │            ├── Install Docker
    │            ├── Install CloudWatch Agent
    │            ├── git clone rag_app
    │            ├── docker build + docker run
    │            └── Health check loop
    ├─9─▶ ALB + Target Group + Listener
    └─10─▶ Budget Alarm
```

**Total provisioning time:** ~8-12 minutes (dominated by EC2 bootstrap and Docker builds)

---

## Terraform Destroy Behavior

```bash
terraform destroy
```

- **Removed:** VPC, subnets, security groups, EC2 instances, ALB, VPC endpoints, IAM roles, log groups, budget
- **Preserved:** RuVector data EBS volume (due to `delete_on_termination = false`)

To completely clean up including data:
```bash
# After terraform destroy, manually delete the orphaned EBS volume
aws ec2 delete-volume --volume-id vol-xxxxxxxxx
```
# RAG Chatbot Deployment Fixes & Lessons Learned

## Project Overview

This project implements a Smart Hybrid RAG system using:

* AWS Bedrock
* Claude 3.5 Haiku
* Amazon Titan Embeddings
* RuVector Database
* FastAPI Backend
* Docker
* Terraform
* EC2

The original deployment failed due to multiple infrastructure and integration issues. This document records all fixes that were applied to achieve a successful deployment.

---

# 1. EC2 Architecture Mismatch

## Problem

Terraform originally deployed:

```hcl
instance_type = "t4g.micro"
```

while using an x86 Ubuntu AMI.

AWS returned:

```text
InvalidParameterValue:
The architecture 'x86_64' of the specified instance type
does not match the architecture 'arm64' of the specified AMI
```

## Fix

Switched to:

```hcl
instance_type = "t3.medium"
```

and Ubuntu x86_64 AMI.

---

# 2. Docker Image Does Not Exist

## Problem

Original deployment referenced:

```yaml
ruvnet/ruvector-server:latest
ruvnet/ruvector-rag-chatbot:latest
```

These images do not exist on Docker Hub.

## Fix

Used:

```yaml
image: ruvnet/ruvector:latest
```

for RuVector database.

Built chatbot image locally:

```yaml
build:
  context: /home/ubuntu/rag_app
  dockerfile: Dockerfile
```

---

# 3. Insufficient Disk Space

## Problem

Docker build failed with:

```text
no space left on device
```

while installing:

* torch
* transformers
* sentence-transformers

Container layers exceeded default root volume size.

## Fix

Terraform updated:

```hcl
root_block_device {
  volume_size = 40
  volume_type = "gp3"
}
```

---

# 4. RuVector REST API Missing

## Problem

RuVector Docker image exposes PostgreSQL and extensions only.

No REST API exists on:

```text
http://ruvector:6333
```

Backend ingestion failed:

```text
All connection attempts failed
```

## Investigation

Compiled:

```bash
cargo build --release -p ruvector-server
```

Discovered:

```text
crates/ruvector-server
```

is a Rust library, not an executable.

No main.rs existed.

---

# 5. Created Custom RuVector Launcher

## Solution

Created:

```text
launcher/
 ├── Cargo.toml
 └── src/main.rs
```

Launcher code:

```rust
use ruvector_server::{Config, RuvectorServer};

#[tokio::main]
async fn main() {
    let config = Config {
        host: "0.0.0.0".to_string(),
        port: 6333,
        enable_cors: true,
        enable_compression: true,
    };

    let server = RuvectorServer::with_config(config);

    server.start().await.unwrap();
}
```

Compiled successfully:

```bash
cargo build --release
```

Started:

```bash
./target/release/ruvector-launcher
```

Health endpoint verified:

```bash
curl http://localhost:6333/health
```

Response:

```json
{
  "status":"healthy"
}
```

---

# 6. Docker Networking Issue

## Problem

Backend attempted:

```text
http://ruvector:6333
```

Result:

```text
Connection refused
```

Reason:

RuVector REST API was running on EC2 host.

Backend was running inside Docker.

---

## Fix

Verified connectivity:

```bash
docker exec rag-chatbot python
```

Tested:

```python
requests.get("http://172.17.0.1:6333/health")
```

Success.

Updated:

```yaml
RUVECTOR_URL=http://172.17.0.1:6333
```

---

# 7. Bedrock IAM Permissions

## Problem

Bedrock invocation would fail without proper IAM permissions.

## Fix

Terraform IAM role updated:

```json
{
  "Action": [
    "bedrock:InvokeModel",
    "bedrock:ListFoundationModels"
  ],
  "Effect": "Allow",
  "Resource": "*"
}
```

Attached to EC2 instance profile.

---

# 8. Embedding Dimension Mismatch

## Problem

Ingestion failed:

```text
Dimension mismatch:
expected 1536
got 384
```

Collection:

```text
finance_docs
```

Expected:

```text
1536
```

Generated embeddings:

```text
384
```

---

## Root Cause

Backend was loading:

```python
SentenceTransformer("all-MiniLM-L6-v2")
```

which generates:

```text
384 dimensions
```

while RuVector collection was created for:

```text
1536 dimensions
```

---

# 9. Verified Amazon Titan Embeddings

Executed:

```python
import boto3
import json

client = boto3.client(
    "bedrock-runtime",
    region_name="us-east-1"
)

response = client.invoke_model(
    modelId="amazon.titan-embed-text-v1",
    body=json.dumps({
        "inputText":"hello world"
    })
)

embedding = json.loads(
    response["body"].read()
)["embedding"]

print(len(embedding))
```

Output:

```text
1536
```

Amazon Titan Text Embeddings G1 returns fixed 1536-dimensional embeddings.

---

# 10. Final Embedding Strategy

Removed local embeddings:

```python
SentenceTransformer("all-MiniLM-L6-v2")
```

Removed:

```text
torch
transformers
sentence-transformers
```

from embedding pipeline.

Replaced with:

```python
amazon.titan-embed-text-v1
```

via Bedrock.

Result:

```text
Collection dimension = 1536
Embedding dimension = 1536
```

Matching successfully.

---

# 11. Final Production Architecture

```text
Internet
    |
    |
FastAPI UI (Docker)
    |
    |
Amazon Titan Embeddings
    |
    |
Claude 3.5 Haiku
    |
    |
RuVector REST Server (6333)
    |
    |
RuVector PostgreSQL (5432)
```

---

# Final Status

Infrastructure:

* Terraform ✅
* VPC ✅
* Security Groups ✅
* EC2 ✅
* IAM Roles ✅

Application:

* Docker ✅
* Docker Compose ✅
* FastAPI ✅
* Frontend UI ✅

AI:

* Amazon Titan Embeddings ✅
* Claude 3.5 Haiku ✅
* Bedrock Runtime ✅

Vector Database:

* RuVector PostgreSQL ✅
* Custom REST Server ✅
* Collection Creation ✅
* Document Ingestion ✅
* Vector Search ✅

Deployment:

* Fully automated via Terraform user_data.sh ✅

---

# Future Improvements

1. Containerize RuVector REST launcher.
2. Replace 172.17.0.1 with Docker service discovery.
3. Add Nginx reverse proxy.
4. Add HTTPS via ACM + ALB.
5. Add CloudWatch logging.
6. Add CI/CD via GitHub Actions.

Version: v1.0 Production Deployment
Date: May 2026

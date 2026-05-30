#!/bin/bash
set -e

exec > >(tee /var/log/user-data.log) 2>&1

echo "===== Starting RAG App Deployment $(date) ====="

# --------------------------------------------------
# System packages
# --------------------------------------------------
apt-get update -y
apt-get install -y \
  ca-certificates \
  curl \
  gnupg \
  git \
  awscli

# --------------------------------------------------
# Install Docker
# --------------------------------------------------
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu jammy stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update -y
apt-get install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

systemctl enable docker
systemctl start docker
echo "Docker installed successfully"

# --------------------------------------------------
# Wait for Qdrant (RuVector instance) to be ready
# before starting the RAG app - otherwise the first
# health check will show ruvector=unavailable and the
# ALB will mark the target unhealthy on first probe.
# --------------------------------------------------
echo "Waiting for Qdrant at ${ruvector_url} to be ready..."
QDRANT_HEALTH="${ruvector_url%:*}:${ruvector_url##*:}/health"
# ruvector_url is http://IP:6333 - build health URL
QDRANT_HOST=$(echo "${ruvector_url}" | sed 's|http://||' | cut -d: -f1)
QDRANT_HEALTH_URL="http://$${QDRANT_HOST}:6333/health"

for i in $(seq 1 30); do
  if curl -sf "$${QDRANT_HEALTH_URL}" > /dev/null 2>&1; then
    echo "Qdrant is reachable!"
    break
  fi
  echo "Qdrant not ready yet... attempt $i/30"
  sleep 10
done

# --------------------------------------------------
# Clone and build RAG App
# --------------------------------------------------
cd /home/ubuntu
rm -rf rag_app

git clone https://github.com/Kedar-Gaikwad/rag_app.git rag_app
chown -R ubuntu:ubuntu /home/ubuntu/rag_app

# --------------------------------------------------
# Build and run RAG App container
# --------------------------------------------------
cd /home/ubuntu/rag_app

docker build -t rag-chatbot -f Dockerfile .

docker run -d \
  --name rag-chatbot \
  --restart unless-stopped \
  --network host \
  -e RUVECTOR_URL="${ruvector_url}" \
  -e EMBEDDING_PROVIDER="${embedding_provider}" \
  -e LLM_PROVIDER="${llm_provider}" \
  -e AWS_REGION="${aws_region}" \
  -e AWS_DEFAULT_REGION="${aws_region}" \
  rag-chatbot

echo "Waiting for RAG App to start..."
sleep 10

# Health check - verify both app and Qdrant connection
for i in $(seq 1 12); do
  HEALTH=$(curl -sf http://localhost:8000/health 2>/dev/null || echo "")
  if echo "$${HEALTH}" | grep -q '"status": "healthy"'; then
    echo "RAG App is healthy!"
    echo "Health response: $${HEALTH}"
    break
  fi
  echo "Waiting for RAG App health check... attempt $i/12"
  sleep 10
done

# Verify Bedrock is reachable via IAM role
echo "Verifying Bedrock connectivity..."
BEDROCK_CHECK=$(curl -sf http://localhost:8000/health/bedrock 2>/dev/null || echo "no-endpoint")
echo "Bedrock check: $${BEDROCK_CHECK}"

echo "===== RAG App Deployment Complete $(date) ====="

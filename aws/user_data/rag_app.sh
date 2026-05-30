#!/bin/bash
# Do NOT use set -e — we want the script to continue even if Qdrant
# isn't reachable yet. The container handles reconnection via restart policy.

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
# Wait for Qdrant to be ready (best-effort, non-blocking).
# ruvector_url is injected by Terraform as http://IP:6333
# --------------------------------------------------
QDRANT_HEALTH_URL="${ruvector_url}/health"
echo "Waiting for Qdrant at $QDRANT_HEALTH_URL ..."

QDRANT_READY=false
for i in $(seq 1 30); do
  if curl -sf "$QDRANT_HEALTH_URL" > /dev/null 2>&1; then
    echo "Qdrant is reachable after $i attempts!"
    QDRANT_READY=true
    break
  fi
  echo "Qdrant not ready yet... attempt $i/30 (sleeping 10s)"
  sleep 10
done

if [ "$QDRANT_READY" = "false" ]; then
  echo "WARNING: Qdrant not reachable after 5 minutes. Starting RAG app anyway."
  echo "The container will retry connections automatically."
fi

# --------------------------------------------------
# Clone and build RAG App
# --------------------------------------------------
cd /home/ubuntu
rm -rf rag_app

git clone https://github.com/Kedar-Gaikwad/rag_app.git rag_app || {
  echo "ERROR: git clone failed"
  exit 1
}
chown -R ubuntu:ubuntu /home/ubuntu/rag_app

# --------------------------------------------------
# Build RAG App Docker image
# --------------------------------------------------
cd /home/ubuntu/rag_app

echo "Building rag-chatbot Docker image..."
docker build -t rag-chatbot -f Dockerfile . || {
  echo "ERROR: docker build failed"
  exit 1
}
echo "Docker image built successfully"

# --------------------------------------------------
# Run RAG App container
# --network host gives the container access to the EC2
# instance metadata service (169.254.169.254) so boto3
# can pick up the IAM role credentials for Bedrock.
# --------------------------------------------------
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

echo "Container started. Waiting 15s for app to initialize..."
sleep 15

# --------------------------------------------------
# Verify container is still running (didn't crash on start)
# --------------------------------------------------
CONTAINER_STATUS=$(docker inspect --format='{{.State.Status}}' rag-chatbot 2>/dev/null || echo "not-found")
echo "Container status: $CONTAINER_STATUS"

if [ "$CONTAINER_STATUS" != "running" ]; then
  echo "ERROR: Container is not running. Last 50 log lines:"
  docker logs --tail 50 rag-chatbot 2>&1 || true
  echo "Attempting restart..."
  docker start rag-chatbot || true
fi

# --------------------------------------------------
# Health check loop
# --------------------------------------------------
echo "Polling /health endpoint..."
for i in $(seq 1 18); do
  HEALTH=$(curl -sf http://localhost:8000/health 2>/dev/null || true)
  if [ -n "$HEALTH" ]; then
    echo "RAG App is responding!"
    echo "Health: $HEALTH"
    break
  fi
  echo "Health check attempt $i/18..."
  sleep 10
done

# --------------------------------------------------
# Final status report
# --------------------------------------------------
echo ""
echo "===== Deployment Status Report ====="
echo "Container state:"
docker inspect --format='Status={{.State.Status}} ExitCode={{.State.ExitCode}}' rag-chatbot 2>/dev/null || echo "Container not found"

echo ""
echo "Last 30 container log lines:"
docker logs --tail 30 rag-chatbot 2>&1 || true

echo ""
echo "Bedrock connectivity check:"
curl -sf http://localhost:8000/health/bedrock 2>/dev/null \
  && echo "Bedrock: OK" \
  || echo "Bedrock: not yet reachable (IAM role may still be propagating)"

echo ""
echo "===== RAG App Deployment Complete $(date) ====="

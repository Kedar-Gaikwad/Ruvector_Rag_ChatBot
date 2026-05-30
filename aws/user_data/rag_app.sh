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
# Configure Docker to use awslogs driver by default
# --------------------------------------------------
REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)

cat > /etc/docker/daemon.json <<EOF
{
  "log-driver": "awslogs",
  "log-opts": {
    "awslogs-region": "$REGION",
    "awslogs-group": "${log_group}",
    "tag": "rag-app/{{.Name}}/{{.ID}}"
  }
}
EOF

systemctl restart docker
echo "Docker configured with awslogs driver"

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
  -p 8000:8000 \
  --log-opt awslogs-stream="rag-chatbot" \
  -e RUVECTOR_URL="${ruvector_url}" \
  -e EMBEDDING_PROVIDER="${embedding_provider}" \
  -e LLM_PROVIDER="${llm_provider}" \
  -e AWS_REGION="${aws_region}" \
  -e AWS_DEFAULT_REGION="${aws_region}" \
  rag-chatbot

echo "Waiting for RAG App to start..."
sleep 10

# Health check
for i in $(seq 1 12); do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo "RAG App is healthy!"
    break
  fi
  echo "Waiting for RAG App health check... attempt $i/12"
  sleep 10
done

echo "===== RAG App Deployment Complete $(date) ====="
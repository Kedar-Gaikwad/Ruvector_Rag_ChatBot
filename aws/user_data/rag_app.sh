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
# Install CloudWatch Logs Agent
# --------------------------------------------------
curl -sO https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb
dpkg -i -E ./amazon-cloudwatch-agent.deb

cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<'CWCONFIG'
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/rag-app/*.log",
            "log_group_name": "${log_group}",
            "log_stream_name": "{instance_id}/rag-app",
            "timestamp_format": "%Y-%m-%dT%H:%M:%S"
          },
          {
            "file_path": "/var/log/user-data.log",
            "log_group_name": "${log_group}",
            "log_stream_name": "{instance_id}/user-data",
            "timestamp_format": "%Y-%m-%dT%H:%M:%S"
          }
        ]
      }
    }
  }
}
CWCONFIG

/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json -s

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
mkdir -p /var/log/rag-app

cd /home/ubuntu/rag_app

docker build -t rag-chatbot -f Dockerfile .

docker run -d \
  --name rag-chatbot \
  --restart unless-stopped \
  -p 8000:8000 \
  -v /var/log/rag-app:/var/log/rag-app \
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
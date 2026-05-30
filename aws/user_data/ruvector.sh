#!/bin/bash
set -e

exec > >(tee /var/log/user-data.log) 2>&1

echo "===== Starting Qdrant (Vector DB) Deployment $(date) ====="

# --------------------------------------------------
# System packages
# --------------------------------------------------
apt-get update -y
apt-get install -y \
  ca-certificates \
  curl \
  gnupg

# --------------------------------------------------
# Mount EBS data volume
# --------------------------------------------------
DATA_MOUNT="/var/lib/qdrant/storage"
mkdir -p $DATA_MOUNT

# Wait for EBS volume to attach - detect NVMe or traditional device names
DATA_DEVICE=""
for i in $(seq 1 30); do
  if [ -b "/dev/xvdf" ]; then
    DATA_DEVICE="/dev/xvdf"
    break
  fi
  if [ -b "/dev/nvme1n1" ]; then
    DATA_DEVICE="/dev/nvme1n1"
    break
  fi
  if [ -b "/dev/sdf" ]; then
    DATA_DEVICE="/dev/sdf"
    break
  fi
  echo "Waiting for EBS volume... attempt $i/30"
  sleep 10
done

if [ -z "$DATA_DEVICE" ]; then
  echo "WARNING: EBS data volume not found. Using root volume for storage."
else
  echo "EBS volume found at $DATA_DEVICE"
  # blkid exits 0 if filesystem found, non-zero if raw/unformatted
  if ! blkid "$DATA_DEVICE" > /dev/null 2>&1; then
    echo "No filesystem detected - formatting new EBS volume..."
    mkfs.ext4 "$DATA_DEVICE"
  else
    echo "Existing filesystem detected - skipping format."
  fi
  mount "$DATA_DEVICE" "$DATA_MOUNT"
  echo "$DATA_DEVICE $DATA_MOUNT ext4 defaults,nofail 0 2" >> /etc/fstab
  echo "EBS volume mounted at $DATA_MOUNT"
fi

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
  containerd.io

systemctl enable docker
systemctl start docker
echo "Docker installed successfully"

# --------------------------------------------------
# Run Qdrant vector database (REST API on port 6333)
# Qdrant REST API used by the RAG app:
#   GET  /health
#   GET  /collections
#   PUT  /collections/{name}          <- create collection
#   DELETE /collections/{name}        <- drop collection
#   PUT  /collections/{name}/points   <- upsert vectors
#   POST /collections/{name}/points/search  <- similarity search
# --------------------------------------------------
docker run -d \
  --name qdrant \
  --restart unless-stopped \
  -p 6333:6333 \
  -p 6334:6334 \
  -v "$DATA_MOUNT":/qdrant/storage \
  qdrant/qdrant:v1.9.2

echo "Qdrant container started, waiting for it to be ready..."
sleep 10

# Health check — Qdrant liveness probe is /healthz
for i in $(seq 1 18); do
  if curl -sf http://localhost:6333/healthz > /dev/null 2>&1; then
    echo "Qdrant is healthy and ready on port 6333!"
    curl -s http://localhost:6333/healthz
    break
  fi
  echo "Waiting for Qdrant... attempt $i/18"
  sleep 10
done

echo "===== Qdrant Deployment Complete $(date) ====="

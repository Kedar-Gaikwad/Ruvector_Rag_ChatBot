#!/bin/bash
set -e

exec > >(tee /var/log/user-data.log) 2>&1

echo "===== Starting RuVector Service Deployment $(date) ====="

# --------------------------------------------------
# System packages
# --------------------------------------------------
apt-get update -y
apt-get install -y \
  ca-certificates \
  curl \
  gnupg \
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
  docker-compose-plugin

systemctl enable docker
systemctl start docker
echo "Docker installed successfully"

# --------------------------------------------------
# Configure Docker to use awslogs driver
# --------------------------------------------------
REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)

cat > /etc/docker/daemon.json <<EOF
{
  "log-driver": "awslogs",
  "log-opts": {
    "awslogs-region": "$REGION",
    "awslogs-group": "${log_group}",
    "tag": "ruvector/{{.Name}}/{{.ID}}"
  }
}
EOF

systemctl restart docker
echo "Docker configured with awslogs driver"

# --------------------------------------------------
# Mount EBS data volume
# --------------------------------------------------
DATA_MOUNT="/var/lib/ruvector/data"
mkdir -p $DATA_MOUNT

# Wait for EBS volume to attach - detect NVMe or traditional device names
DATA_DEVICE=""
for i in $(seq 1 30); do
  # Check traditional name
  if [ -b "/dev/xvdf" ]; then
    DATA_DEVICE="/dev/xvdf"
    break
  fi
  # Check NVMe name (common on t3/m5/c5 instances)
  if [ -b "/dev/nvme1n1" ]; then
    DATA_DEVICE="/dev/nvme1n1"
    break
  fi
  # Also check /dev/sdf (symlink on some AMIs)
  if [ -b "/dev/sdf" ]; then
    DATA_DEVICE="/dev/sdf"
    break
  fi
  echo "Waiting for EBS volume... attempt $i/30"
  sleep 10
done

if [ -z "$DATA_DEVICE" ]; then
  echo "ERROR: EBS data volume not found after 5 minutes. Skipping mount."
  echo "RuVector will use root volume for data storage."
else
  echo "EBS volume found at $DATA_DEVICE"

  # Format only if not already formatted
  if ! blkid $DATA_DEVICE; then
    echo "Formatting new EBS volume..."
    mkfs.ext4 $DATA_DEVICE
  fi

  mount $DATA_DEVICE $DATA_MOUNT
  echo "$DATA_DEVICE $DATA_MOUNT ext4 defaults,nofail 0 2" >> /etc/fstab
  echo "EBS volume mounted at $DATA_MOUNT"
fi

# --------------------------------------------------
# Run RuVector container
# --------------------------------------------------
docker run -d \
  --name ruvector-db \
  --restart unless-stopped \
  -p 6333:6333 \
  -v $DATA_MOUNT:/var/lib/ruvector/data \
  --log-opt awslogs-stream="ruvector-db" \
  ruvnet/ruvector:latest

echo "Waiting for RuVector to start..."
sleep 15

# Health check
for i in $(seq 1 12); do
  if curl -sf http://localhost:6333/health > /dev/null 2>&1; then
    echo "RuVector is healthy!"
    break
  fi
  echo "Waiting for RuVector health check... attempt $i/12"
  sleep 10
done

echo "===== RuVector Service Deployment Complete $(date) ====="
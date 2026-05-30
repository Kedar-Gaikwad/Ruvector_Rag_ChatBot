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
# Mount EBS data volume
# --------------------------------------------------
DATA_DEVICE="/dev/xvdf"
DATA_MOUNT="/var/lib/ruvector/data"

mkdir -p $DATA_MOUNT

# Wait for EBS volume to attach
for i in $(seq 1 30); do
  if [ -b "$DATA_DEVICE" ]; then
    echo "EBS volume found at $DATA_DEVICE"
    break
  fi
  echo "Waiting for EBS volume... attempt $i/30"
  sleep 10
done

# Format only if not already formatted
if ! blkid $DATA_DEVICE; then
  echo "Formatting new EBS volume..."
  mkfs.ext4 $DATA_DEVICE
fi

mount $DATA_DEVICE $DATA_MOUNT
echo "$DATA_DEVICE $DATA_MOUNT ext4 defaults,nofail 0 2" >> /etc/fstab
echo "EBS volume mounted at $DATA_MOUNT"

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
            "file_path": "/var/log/ruvector/*.log",
            "log_group_name": "${log_group}",
            "log_stream_name": "{instance_id}/ruvector",
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
# Run RuVector container
# --------------------------------------------------
mkdir -p /var/log/ruvector

docker run -d \
  --name ruvector-db \
  --restart unless-stopped \
  -p 6333:6333 \
  -v $DATA_MOUNT:/var/lib/ruvector/data \
  -v /var/log/ruvector:/var/log/ruvector \
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

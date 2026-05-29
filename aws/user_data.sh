#!/bin/bash
set -e

exec > >(tee /var/log/user-data.log)
exec 2>&1

echo "===== Starting deployment $(date) ====="

# --------------------------------------------------

# System packages

# --------------------------------------------------

apt-get update -y
apt-get install -y 
ca-certificates 
curl 
gnupg 
git 
build-essential

# --------------------------------------------------

# Docker

# --------------------------------------------------

install -m 0755 -d /etc/apt/keyrings

curl -fsSL https://download.docker.com/linux/ubuntu/gpg 
| gpg --dearmor -o /etc/apt/keyrings/docker.gpg

chmod a+r /etc/apt/keyrings/docker.gpg

echo 
"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu jammy stable" \

> /etc/apt/sources.list.d/docker.list

apt-get update -y

apt-get install -y 
docker-ce 
docker-ce-cli 
containerd.io 
docker-buildx-plugin 
docker-compose-plugin

systemctl enable docker
systemctl start docker

echo "Docker installed"

# --------------------------------------------------

# Clone repositories

# --------------------------------------------------

cd /home/ubuntu

rm -rf ruvector_src
rm -rf rag_app

git clone https://github.com/ruvnet/ruvector.git ruvector_src
git clone https://github.com/Kedar-Gaikwad/rag_app.git rag_app

chown -R ubuntu:ubuntu /home/ubuntu/ruvector_src
chown -R ubuntu:ubuntu /home/ubuntu/rag_app

# --------------------------------------------------

# Install Rust

# --------------------------------------------------

su - ubuntu -c '
curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
'

# --------------------------------------------------

# Create launcher

# --------------------------------------------------

mkdir -p /home/ubuntu/ruvector_src/launcher/src

cat > /home/ubuntu/ruvector_src/launcher/Cargo.toml <<EOF
[package]
name = "ruvector-launcher"
version = "0.1.0"
edition = "2024"

[dependencies]
tokio = { version = "1", features = ["full"] }
ruvector-server = { path = "../crates/ruvector-server" }

[workspace]
EOF

cat > /home/ubuntu/ruvector_src/launcher/src/main.rs <<EOF
use ruvector_server::{Config, RuvectorServer};

#[tokio::main]
async fn main() {
let config = Config {
host: "0.0.0.0".to_string(),
port: 6333,
enable_cors: true,
enable_compression: true,
};

```
let server = RuvectorServer::with_config(config);

println!("Starting RuVector REST server on 0.0.0.0:6333");

if let Err(e) = server.start().await {
    eprintln!("Server error: {:?}", e);
}
```

}
EOF

chown -R ubuntu:ubuntu /home/ubuntu/ruvector_src/launcher

# --------------------------------------------------

# Build launcher

# --------------------------------------------------

su - ubuntu -c '
export PATH=$HOME/.cargo/bin:$PATH
cd /home/ubuntu/ruvector_src/launcher
cargo build --release
'

# --------------------------------------------------

# Start launcher

# --------------------------------------------------

nohup /home/ubuntu/ruvector_src/launcher/target/release/ruvector-launcher \

> /var/log/ruvector-server.log 2>&1 &

sleep 15

curl http://localhost:6333/health || true

# --------------------------------------------------

# Docker Compose

# --------------------------------------------------

cat > /home/ubuntu/docker-compose.yml <<EOF
services:

ruvector-db:
image: ruvnet/ruvector:latest
container_name: ruvector-db
restart: unless-stopped
ports:
- "5432:5432"

rag-chatbot:
build:
context: /home/ubuntu/rag_app
dockerfile: Dockerfile
container_name: rag-chatbot
restart: unless-stopped
ports:
- "80:8000"
environment:
- RUVECTOR_URL=http://172.17.0.1:6333
- EMBEDDING_PROVIDER=${embedding_provider}
- LLM_PROVIDER=${llm_provider}
- AWS_REGION=${aws_region}
- OPENAI_API_KEY=${openai_api_key}
depends_on:
- ruvector-db
EOF

cd /home/ubuntu

docker compose build
docker compose up -d

echo "===== Deployment complete ====="

docker ps -a

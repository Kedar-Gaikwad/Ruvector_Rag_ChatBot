#!/bin/bash
# deploy.sh - Pull latest code and restart the RAG app container on EC2
# Usage: ./deploy.sh <instance-id> <qdrant-private-ip>
# Example: ./deploy.sh i-0abc123def456 10.0.1.225
#
# Requires: AWS CLI configured, SSM access to the instance

set -e

INSTANCE_ID="${1:?Usage: ./deploy.sh <instance-id> <qdrant-private-ip>}"
QDRANT_IP="${2:?Usage: ./deploy.sh <instance-id> <qdrant-private-ip>}"
REGION="${AWS_REGION:-us-east-1}"

echo "Deploying to instance $INSTANCE_ID (Qdrant: $QDRANT_IP)..."

COMMAND_ID=$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --parameters "commands=[
    'set -e',
    'cd /home/ubuntu/rag_app && git pull',
    'docker build -t rag-chatbot -f Dockerfile .',
    'docker stop rag-chatbot || true',
    'docker rm rag-chatbot || true',
    'docker run -d --name rag-chatbot --restart unless-stopped --network host -e RUVECTOR_URL=http://${QDRANT_IP}:6333 -e EMBEDDING_PROVIDER=bedrock -e LLM_PROVIDER=bedrock -e AWS_REGION=${REGION} -e AWS_DEFAULT_REGION=${REGION} rag-chatbot',
    'sleep 10',
    'curl -sf http://localhost:8000/health'
  ]" \
  --region "$REGION" \
  --query "Command.CommandId" \
  --output text)

echo "SSM Command ID: $COMMAND_ID"
echo "Waiting for command to complete..."

aws ssm wait command-executed \
  --command-id "$COMMAND_ID" \
  --instance-id "$INSTANCE_ID" \
  --region "$REGION"

echo ""
echo "Output:"
aws ssm get-command-invocation \
  --command-id "$COMMAND_ID" \
  --instance-id "$INSTANCE_ID" \
  --region "$REGION" \
  --query "[StandardOutputContent, StandardErrorContent]" \
  --output text

echo ""
echo "Deploy complete."

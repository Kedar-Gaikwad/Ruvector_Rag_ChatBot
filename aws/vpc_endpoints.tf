# ============================================================================
# VPC ENDPOINTS - Private AWS service access (no NAT Gateway needed)
# Both EC2 instances are in the public subnet and have internet access for
# git clone / docker pull. VPC endpoints keep Bedrock and SSM calls inside
# the AWS network (lower latency, no data transfer cost).
#
# depends_on = [aws_security_group.vpc_endpoints] ensures Terraform destroys
# the endpoints BEFORE the SG, preventing DependencyViolation on terraform destroy.
# ============================================================================

# Bedrock Runtime - embedding (Titan) and LLM (Claude) calls from RAG App
resource "aws_vpc_endpoint" "bedrock" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.bedrock-runtime"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.public.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = { Name = "ruvector-rag-bedrock-vpce" }

  depends_on = [aws_security_group.vpc_endpoints]
}

# ECR API - image metadata
resource "aws_vpc_endpoint" "ecr_api" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ecr.api"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.public.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = { Name = "ruvector-rag-ecr-api-vpce" }

  depends_on = [aws_security_group.vpc_endpoints]
}

# ECR DKR - image layer pulls
resource "aws_vpc_endpoint" "ecr_dkr" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.public.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = { Name = "ruvector-rag-ecr-dkr-vpce" }

  depends_on = [aws_security_group.vpc_endpoints]
}

# S3 Gateway - ECR image layer storage (free, no hourly charge)
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]

  tags = { Name = "ruvector-rag-s3-vpce" }
}

# SSM - Systems Manager core
resource "aws_vpc_endpoint" "ssm" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ssm"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.public.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = { Name = "ruvector-rag-ssm-vpce" }

  depends_on = [aws_security_group.vpc_endpoints]
}

# SSM Messages - Session Manager shell access
resource "aws_vpc_endpoint" "ssm_messages" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ssmmessages"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.public.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = { Name = "ruvector-rag-ssmmessages-vpce" }

  depends_on = [aws_security_group.vpc_endpoints]
}

# EC2 Messages - required for SSM agent communication
resource "aws_vpc_endpoint" "ec2_messages" {
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.ec2messages"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.public.id]
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = { Name = "ruvector-rag-ec2messages-vpce" }

  depends_on = [aws_security_group.vpc_endpoints]
}

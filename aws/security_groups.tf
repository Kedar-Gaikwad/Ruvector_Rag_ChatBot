# ============================================================================
# SECURITY GROUPS
# ============================================================================

# ALB Security Group - Public HTTP access
resource "aws_security_group" "alb" {
  name        = "ruvector-rag-alb-sg"
  description = "Allow inbound HTTP to ALB from anywhere"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP from internet"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "ruvector-rag-alb-sg"
  }
}

# RAG App Security Group
# Accepts traffic from ALB on 8000 and SSH from admin via SSM (no direct SSH needed)
resource "aws_security_group" "rag_app" {
  name        = "ruvector-rag-app-sg"
  description = "Allow traffic to RAG App from ALB only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "HTTP from ALB"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description = "SSH from admin"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_ip_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "ruvector-rag-app-sg"
  }
}

# RuVector Security Group - Only from RAG App on port 6333
resource "aws_security_group" "ruvector" {
  name        = "ruvector-ruvector-sg"
  description = "Allow Qdrant REST API only from RAG App security group"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Qdrant REST API from RAG App"
    from_port       = 6333
    to_port         = 6333
    protocol        = "tcp"
    security_groups = [aws_security_group.rag_app.id]
  }

  ingress {
    description = "Qdrant REST API from admin (debugging)"
    from_port   = 6333
    to_port     = 6333
    protocol    = "tcp"
    cidr_blocks = [var.admin_ip_cidr]
  }

  ingress {
    description = "SSH from admin"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_ip_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "ruvector-ruvector-sg"
  }
}

# VPC Endpoints Security Group
# Instances are in the public subnet (10.0.1.0/24) - allow HTTPS inbound from there
resource "aws_security_group" "vpc_endpoints" {
  name        = "ruvector-rag-vpce-sg"
  description = "Allow HTTPS from public subnet to VPC endpoints"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTPS from public subnet"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["10.0.1.0/24"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "ruvector-rag-vpce-sg"
  }
}

# ============================================================================
# COMPUTE - EC2 Instances
# ============================================================================

# Ubuntu 22.04 AMI lookup
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# --- RAG App EC2 Instance (Spot) ---
# Placed in public subnet for internet access (git clone, docker pull)
resource "aws_spot_instance_request" "rag_app" {
  ami                            = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu.id
  instance_type                  = var.rag_app_instance_type
  subnet_id                      = aws_subnet.public.id
  vpc_security_group_ids         = [aws_security_group.rag_app.id]
  iam_instance_profile           = aws_iam_instance_profile.rag_app.name
  associate_public_ip_address    = true
  spot_type                      = "persistent"
  instance_interruption_behavior = "stop"
  wait_for_fulfillment           = true

  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    delete_on_termination = true
  }

  user_data = base64encode(templatefile("${path.module}/user_data/rag_app.sh", {
    ruvector_url       = "http://${aws_instance.ruvector.private_ip}:6333"
    embedding_provider = var.embedding_provider
    llm_provider       = var.llm_provider
    aws_region         = var.aws_region
    log_group          = aws_cloudwatch_log_group.rag_app.name
  }))

  tags = {
    Name = "ruvector-rag-app-spot"
  }
}

# --- RuVector EC2 Instance (On-Demand for data persistence) ---
# Placed in public subnet for internet access (docker pull ruvnet/ruvector)
resource "aws_instance" "ruvector" {
  ami                         = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu.id
  instance_type               = var.ruvector_instance_type
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.ruvector.id]
  iam_instance_profile        = aws_iam_instance_profile.ruvector.name
  associate_public_ip_address = true

  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    delete_on_termination = true
  }

  user_data = base64encode(templatefile("${path.module}/user_data/ruvector.sh", {
    aws_region = var.aws_region
    log_group  = aws_cloudwatch_log_group.ruvector.name
  }))

  user_data_replace_on_change = true

  tags = {
    Name = "ruvector-ruvector-service"
  }
}
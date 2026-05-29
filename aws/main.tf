provider "aws" {
  region = var.aws_region
}

# --- NETWORK LAYER ---
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags = {
    Name = "ruvector-rag-vpc"
  }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  map_public_ip_on_launch = true
  availability_zone       = "${var.aws_region}a"
  tags = {
    Name = "ruvector-rag-public-subnet"
  }
}

resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.main.id
  tags = {
    Name = "ruvector-rag-igw"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.gw.id
  }
  tags = {
    Name = "ruvector-rag-rt"
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# --- SECURITY GROUPS ---
resource "aws_security_group" "ec2" {
  name        = "ruvector-rag-ec2-sg"
  description = "Allow HTTP and SSH to RAG Chatbot instance"
  vpc_id      = aws_vpc.main.id

  # HTTP Web Interface
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Direct access to FastAPI Backend if needed
  ingress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # SSH Access (Optional, System Manager Session Manager is recommended)
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.admin_ip_cidr]
  }

  # Outbound to everywhere (pull docker images, access OpenAI/Bedrock APIs)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "ruvector-rag-sg"
  }
}

# --- IAM ROLE FOR SYSTEMS MANAGER ---
# Allows secure access to the instance terminal without open SSH ports
resource "aws_iam_role" "ssm_role" {
  name = "ruvector-rag-ssm-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_policy" {
  role       = aws_iam_role.ssm_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Bedrock execution permissions (if using AWS Bedrock LLMs)
resource "aws_iam_policy" "bedrock_access" {
  name        = "ruvector-rag-bedrock-policy"
  description = "Allows the RAG app to call Bedrock models securely"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:ListFoundationModels"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "bedrock_policy" {
  role       = aws_iam_role.ssm_role.name
  policy_arn = aws_iam_policy.bedrock_access.arn
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "ruvector-rag-instance-profile"
  role = aws_iam_role.ssm_role.name
}

# --- EC2 VIRTUAL MACHINE ---
# Deploys a super-cheap arm64 t4g.micro instance (free tier eligible, otherwise ~$3.20/month)
resource "aws_instance" "chatbot" {
  ami = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu_x86.id
  instance_type        = "t3.medium"
  subnet_id            = aws_subnet.public.id
  security_groups      = [aws_security_group.ec2.id]
  iam_instance_profile = aws_iam_instance_profile.ec2_profile.name

  root_block_device {
    volume_size           = 40 # 40 GB of persistent EBS SSD storage ($1.20/month)
    volume_type           = "gp3"
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/user_data.sh", {
    embedding_provider = var.embedding_provider
    llm_provider       = var.llm_provider
    openai_api_key     = var.openai_api_key
    aws_region         = var.aws_region
  })

  # Recreate the instance if the startup script changes
  user_data_replace_on_change = true

  tags = {
    Name = "ruvector-rag-chatbot"
  }
}

# --- DATA SEARCH FOR UBUNTU ARM64 ---
data "aws_ami" "ubuntu_x86" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

output "chatbot_public_ip" {
  value       = aws_instance.chatbot.public_ip
  description = "The public IP of your ultra-cheap RAG Chatbot. Access it at http://<this-ip>"
}

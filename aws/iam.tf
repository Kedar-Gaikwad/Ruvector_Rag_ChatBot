# ============================================================================
# IAM ROLES AND POLICIES
# ============================================================================

# --- RAG App IAM Role ---
resource "aws_iam_role" "rag_app" {
  name = "ruvector-rag-app-role"

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

  tags = {
    Name = "ruvector-rag-app-role"
  }
}

# SSM - Session Manager shell access (no bastion host needed)
resource "aws_iam_role_policy_attachment" "rag_app_ssm" {
  role       = aws_iam_role.rag_app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Bedrock - Titan embeddings + Claude LLM
resource "aws_iam_policy" "bedrock_access" {
  name        = "ruvector-rag-bedrock-policy"
  description = "Allows RAG app to invoke Bedrock models for embeddings and LLM"

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

resource "aws_iam_role_policy_attachment" "rag_app_bedrock" {
  role       = aws_iam_role.rag_app.name
  policy_arn = aws_iam_policy.bedrock_access.arn
}

# ECR - pull qdrant and rag-chatbot images
resource "aws_iam_policy" "ecr_access" {
  name        = "ruvector-rag-ecr-policy"
  description = "Allows EC2 instances to pull images from ECR and Docker Hub via ECR endpoints"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "rag_app_ecr" {
  role       = aws_iam_role.rag_app.name
  policy_arn = aws_iam_policy.ecr_access.arn
}

resource "aws_iam_instance_profile" "rag_app" {
  name = "ruvector-rag-app-instance-profile"
  role = aws_iam_role.rag_app.name
}

# --- RuVector IAM Role ---
resource "aws_iam_role" "ruvector" {
  name = "ruvector-ruvector-role"

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

  tags = {
    Name = "ruvector-ruvector-role"
  }
}

# SSM - Session Manager shell access for RuVector instance
resource "aws_iam_role_policy_attachment" "ruvector_ssm" {
  role       = aws_iam_role.ruvector.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# ECR - pull qdrant image
resource "aws_iam_role_policy_attachment" "ruvector_ecr" {
  role       = aws_iam_role.ruvector.name
  policy_arn = aws_iam_policy.ecr_access.arn
}

resource "aws_iam_instance_profile" "ruvector" {
  name = "ruvector-ruvector-instance-profile"
  role = aws_iam_role.ruvector.name
}

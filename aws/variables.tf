# ============================================================================
# INPUT VARIABLES
# ============================================================================

variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "AWS Region to deploy the multi-service RAG architecture"
}

variable "ami_id" {
  type        = string
  default     = ""
  description = "Optional AMI ID override. Defaults to Ubuntu 22.04 amd64"
}

variable "admin_ip_cidr" {
  type        = string
  default     = "0.0.0.0/0"
  description = "CIDR block allowed for SSH access. Set to your IP for security."
}

variable "rag_app_instance_type" {
  type        = string
  default     = "t3.medium"
  description = "Instance type for the RAG App (Spot)"
}

variable "ruvector_instance_type" {
  type        = string
  default     = "t3.medium"
  description = "Instance type for the RuVector Service (On-Demand)"
}

variable "embedding_provider" {
  type        = string
  default     = "bedrock"
  description = "Embedding provider: 'bedrock'"
}

variable "llm_provider" {
  type        = string
  default     = "bedrock"
  description = "LLM provider: 'bedrock' or 'mock'"
}

variable "budget_alert_email" {
  type        = string
  default     = "admin@example.com"
  description = "Email address for AWS Budget alerts"
}

variable "key_pair_name" {
  type        = string
  default     = ""
  description = "Optional EC2 key pair name for SSH access"
}
variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "AWS Region to deploy the low-cost RAG Chatbot"
}

variable "ami_id" {
  type        = string
  default     = ""
  description = "Optional AMI ID override. Defaults to standard Ubuntu Arm64 image"
}

variable "admin_ip_cidr" {
  type        = string
  default     = "0.0.0.0/0"
  description = "CIDR block allowed to SSH directly (if SSH port is open). Leave default or set to your home IP."
}

# --- APPLICATION VARIABLES ---
variable "embedding_provider" {
  type        = string
  default     = "local"
  description = "Embedding provider to use: 'local' (zero-cost) or 'openai' / 'bedrock'"
}

variable "llm_provider" {
  type        = string
  default     = "openai"
  description = "LLM provider: 'openai' or 'bedrock' or 'mock'"
}

variable "openai_api_key" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Optional: OpenAI API Key. Leave blank if using AWS Bedrock or Mock offline mode"
}

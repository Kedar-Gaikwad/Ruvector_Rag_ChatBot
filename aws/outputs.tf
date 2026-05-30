# ============================================================================
# OUTPUTS
# ============================================================================

output "alb_dns_name" {
  value       = aws_lb.main.dns_name
  description = "RAG Chatbot URL: http://<this-value>"
}

output "rag_app_instance_id" {
  value       = aws_spot_instance_request.rag_app.spot_instance_id
  description = "Instance ID of the RAG App Spot instance"
}

output "rag_app_public_ip" {
  value       = aws_spot_instance_request.rag_app.public_ip
  description = "Public IP of the RAG App instance (for SSH)"
}

output "ruvector_instance_id" {
  value       = aws_instance.ruvector.id
  description = "Instance ID of the RuVector (Qdrant) On-Demand instance"
}

output "ruvector_public_ip" {
  value       = aws_instance.ruvector.public_ip
  description = "Public IP of the RuVector instance (for SSH)"
}

output "ruvector_private_ip" {
  value       = aws_instance.ruvector.private_ip
  description = "Private IP used by RAG App to reach Qdrant on port 6333"
}

output "ssh_rag_app" {
  value       = var.key_pair_name != "" ? "ssh -i ~/.ssh/${var.key_pair_name}.pem ubuntu@${aws_spot_instance_request.rag_app.public_ip}" : "No key pair configured"
  description = "SSH command for RAG App instance"
}

output "ssh_ruvector" {
  value       = var.key_pair_name != "" ? "ssh -i ~/.ssh/${var.key_pair_name}.pem ubuntu@${aws_instance.ruvector.public_ip}" : "No key pair configured"
  description = "SSH command for RuVector instance"
}

output "qdrant_health_check" {
  value       = "curl http://${aws_instance.ruvector.public_ip}:6333/health"
  description = "Quick command to verify Qdrant is up (run from your machine)"
}

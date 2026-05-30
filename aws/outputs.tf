# ============================================================================
# OUTPUTS
# ============================================================================

output "alb_dns_name" {
  value       = aws_lb.main.dns_name
  description = "Public DNS name of the ALB. Access the RAG chatbot at http://<this-dns>"
}

output "rag_app_instance_id" {
  value       = aws_spot_instance_request.rag_app.spot_instance_id
  description = "Instance ID of the RAG App Spot instance"
}

output "ruvector_instance_id" {
  value       = aws_instance.ruvector.id
  description = "Instance ID of the RuVector On-Demand instance"
}

output "ruvector_private_ip" {
  value       = aws_instance.ruvector.private_ip
  description = "Private IP of the RuVector instance (used by RAG App)"
}

output "rag_app_log_group" {
  value       = aws_cloudwatch_log_group.rag_app.name
  description = "CloudWatch Log Group for RAG App"
}

output "ruvector_log_group" {
  value       = aws_cloudwatch_log_group.ruvector.name
  description = "CloudWatch Log Group for RuVector"
}
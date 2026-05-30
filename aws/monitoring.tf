# ============================================================================
# MONITORING - CloudWatch Log Groups and Budget Alarm
# ============================================================================

# CloudWatch Log Group for RAG App
resource "aws_cloudwatch_log_group" "rag_app" {
  name              = "/ruvector-rag/rag-app"
  retention_in_days = 7

  tags = {
    Name    = "ruvector-rag-app-logs"
    Service = "rag-app"
  }
}

# CloudWatch Log Group for RuVector Service
resource "aws_cloudwatch_log_group" "ruvector" {
  name              = "/ruvector-rag/ruvector"
  retention_in_days = 7

  tags = {
    Name    = "ruvector-ruvector-logs"
    Service = "ruvector"
  }
}

# AWS Budget - $50/month alarm
resource "aws_budgets_budget" "monthly" {
  name         = "ruvector-rag-monthly-budget"
  budget_type  = "COST"
  limit_amount = "50"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
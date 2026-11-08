# Presupuesto mensual de la cuenta con alertas por correo.
# Las 2 primeras budgets de una cuenta son gratis.
#
# Avisa cuando el gasto real pasa cada umbral de var.actual_thresholds_percent y cuando
# el pronóstico de fin de mes pasa el 100 %.

resource "aws_budgets_budget" "monthly" {
  name         = var.name
  budget_type  = "COST"
  limit_amount = format("%.2f", var.limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = toset(var.actual_thresholds_percent)
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = var.emails
    }
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.emails
  }
}

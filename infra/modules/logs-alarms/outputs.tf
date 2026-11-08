output "log_group_names" {
  description = "Log group de cada servicio."
  value       = { for k, g in aws_cloudwatch_log_group.service : k => g.name }
}

output "log_group_arns" {
  description = "ARN de cada log group."
  value       = { for k, g in aws_cloudwatch_log_group.service : k => g.arn }
}

output "sns_topic_arn" {
  description = "Tema SNS de alertas."
  value       = aws_sns_topic.alerts.arn
}

output "alarm_names" {
  description = "Nombres de todas las alarmas."
  value = concat(
    [for a in aws_cloudwatch_metric_alarm.service_down : a.alarm_name],
    [
      aws_cloudwatch_metric_alarm.alb_5xx.alarm_name,
      aws_cloudwatch_metric_alarm.api_no_healthy_targets.alarm_name,
      aws_cloudwatch_metric_alarm.drawdown.alarm_name,
      aws_cloudwatch_metric_alarm.kill_switch.alarm_name,
    ],
  )
}

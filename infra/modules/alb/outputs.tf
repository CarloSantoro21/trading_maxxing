output "dns_name" {
  description = "DNS público del ALB."
  value       = aws_lb.this.dns_name
}

output "security_group_id" {
  description = "Security group del ALB (las tareas de la api lo aceptan como origen)."
  value       = aws_security_group.alb.id
}

output "api_target_group_arn" {
  description = "Target group de la api."
  value       = aws_lb_target_group.api.arn
}

output "arn_suffix" {
  description = "Sufijo del ARN del ALB (dimensión LoadBalancer de CloudWatch)."
  value       = aws_lb.this.arn_suffix
}

output "api_target_group_arn_suffix" {
  description = "Sufijo del ARN del target group (dimensión TargetGroup de CloudWatch)."
  value       = aws_lb_target_group.api.arn_suffix
}

output "listener_rule_arn" {
  description = "Regla /api/*; el servicio de ECS depende de ella para registrarse."
  value       = aws_lb_listener_rule.api.arn
}

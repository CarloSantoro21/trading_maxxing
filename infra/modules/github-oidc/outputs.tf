output "plan_role_arn" {
  description = "Rol para terraform plan (secret AWS_PLAN_ROLE_ARN del repo)."
  value       = aws_iam_role.plan.arn
}

output "apply_role_arns" {
  description = "Rol de apply por entorno (secret AWS_APPLY_ROLE_ARN de cada GitHub Environment)."
  value       = { for k, r in aws_iam_role.apply : k => r.arn }
}

output "oidc_provider_arn" {
  description = "ARN del proveedor OIDC de GitHub."
  value       = local.oidc_provider_arn
}

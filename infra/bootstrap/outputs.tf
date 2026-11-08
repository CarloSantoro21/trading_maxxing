output "state_bucket" {
  description = "Bucket del estado: va en el backend.hcl de cada entorno."
  value       = aws_s3_bucket.state.bucket
}

output "lock_table" {
  description = "Tabla del lock: va en el backend.hcl de cada entorno."
  value       = aws_dynamodb_table.lock.name
}

output "backend_config" {
  description = "Contenido sugerido de infra/envs/<entorno>/backend.hcl."
  value       = <<-EOT
    bucket         = "${aws_s3_bucket.state.bucket}"
    dynamodb_table = "${aws_dynamodb_table.lock.name}"
    region         = "${var.region}"
    encrypt        = true
  EOT
}

output "github_plan_role_arn" {
  description = "Rol de plan para GitHub Actions (null si OIDC está desactivado)."
  value       = var.enable_github_oidc ? module.github_oidc[0].plan_role_arn : null
}

output "github_apply_role_arns" {
  description = "Roles de apply por entorno (vacío si OIDC está desactivado)."
  value       = var.enable_github_oidc ? module.github_oidc[0].apply_role_arns : {}
}

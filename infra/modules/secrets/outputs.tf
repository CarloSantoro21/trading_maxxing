output "arns" {
  description = "ARN de cada secreto, por nombre corto (binance-testnet, supabase, groq, cryptopanic)."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.arn }
}

output "names" {
  description = "Nombre completo de cada secreto (para cargar el valor con la CLI)."
  value       = { for k, s in aws_secretsmanager_secret.this : k => s.name }
}

output "keys" {
  description = "Llaves JSON esperadas en cada secreto."
  value       = { for k, s in var.secrets : k => s.keys }
}

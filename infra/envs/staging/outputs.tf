output "alb_dns_name" {
  description = "DNS del ALB. La api queda en http://<dns>/api/..."
  value       = module.alb.dns_name
}

output "cluster_name" {
  description = "Cluster de ECS."
  value       = module.ecs.cluster_name
}

output "service_names" {
  description = "Servicios de ECS."
  value       = module.ecs.service_names
}

output "images" {
  description = "Imagen de cada servicio."
  value       = module.ecs.images
}

output "secret_names" {
  description = "Secretos que hay que llenar con `aws secretsmanager put-secret-value`."
  value       = module.secrets.names
}

output "secret_keys" {
  description = "Llaves JSON que espera cada secreto."
  value       = module.secrets.keys
}

output "artifacts_bucket" {
  description = "Bucket de artefactos."
  value       = module.s3.bucket_name
}

output "alerts_topic_arn" {
  description = "Tema SNS de alertas."
  value       = module.logs_alarms.sns_topic_arn
}

output "alarm_names" {
  description = "Alarmas de CloudWatch."
  value       = module.logs_alarms.alarm_names
}

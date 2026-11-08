output "cluster_name" {
  description = "Nombre del cluster."
  value       = aws_ecs_cluster.this.name
}

output "cluster_arn" {
  description = "ARN del cluster."
  value       = aws_ecs_cluster.this.arn
}

output "service_names" {
  description = "Nombre de cada servicio de ECS."
  value       = { for k, s in aws_ecs_service.this : k => s.name }
}

output "service_connect_namespace" {
  description = "Namespace de Service Connect."
  value       = aws_service_discovery_http_namespace.this.name
}

output "app_security_group_id" {
  description = "Security group de api, agents, engine y forecast."
  value       = aws_security_group.app.id
}

output "redis_security_group_id" {
  description = "Security group de redis."
  value       = aws_security_group.redis.id
}

output "execution_role_arn" {
  description = "Rol de ejecución de las tareas (el deploy necesita iam:PassRole sobre él)."
  value       = local.execution_role_arn
}

output "task_role_arns" {
  description = "Rol de tarea de cada servicio."
  value       = local.task_role_arns
}

output "images" {
  description = "Imagen que usa cada servicio."
  value       = { for k, s in local.services : k => s.image }
}

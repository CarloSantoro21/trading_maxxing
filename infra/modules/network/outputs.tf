output "vpc_id" {
  description = "ID de la VPC."
  value       = aws_vpc.this.id
}

output "vpc_cidr_block" {
  description = "CIDR de la VPC."
  value       = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  description = "Subredes públicas (ALB y tareas de Fargate con IP pública)."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Subredes privadas, sin salida a internet."
  value       = aws_subnet.private[*].id
}

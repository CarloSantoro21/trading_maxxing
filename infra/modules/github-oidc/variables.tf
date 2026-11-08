variable "github_repository" {
  description = "Repositorio de GitHub en formato dueño/nombre."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository debe tener la forma dueño/nombre."
  }
}

variable "resource_prefix" {
  description = "Prefijo de todos los recursos del proyecto; los permisos se acotan a él."
  type        = string
  default     = "trading-maxxing"
}

variable "region" {
  description = "Región donde el rol de apply puede crear recursos."
  type        = string
}

variable "environments" {
  description = "Entornos de GitHub que tienen un rol de apply."
  type        = list(string)
  default     = ["staging", "prod"]
}

variable "create_oidc_provider" {
  description = "Crea el proveedor OIDC de GitHub (solo uno por cuenta)."
  type        = bool
  default     = true
}

variable "existing_oidc_provider_arn" {
  description = "ARN de un proveedor OIDC ya existente si create_oidc_provider = false."
  type        = string
  default     = null
}

variable "state_bucket_arn" {
  description = "ARN del bucket del estado de Terraform."
  type        = string
}

variable "lock_table_arn" {
  description = "ARN de la tabla de DynamoDB del lock."
  type        = string
}

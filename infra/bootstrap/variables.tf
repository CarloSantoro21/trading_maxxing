variable "region" {
  description = "Región de AWS."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Prefijo de los recursos."
  type        = string
  default     = "trading-maxxing"
}

variable "github_repository" {
  description = "Repositorio de GitHub (dueño/nombre) que puede asumir los roles."
  type        = string
  default     = "CarloSantoro21/trading_maxxing"
}

variable "enable_github_oidc" {
  description = "Crea el proveedor OIDC y los roles de GitHub Actions. false en AWS Academy (no permite IAM)."
  type        = bool
  default     = true
}

variable "existing_oidc_provider_arn" {
  description = "ARN del proveedor OIDC de GitHub si la cuenta ya tiene uno."
  type        = string
  default     = null
}

variable "budget_limit_usd" {
  description = "Límite mensual del presupuesto en USD."
  type        = number
  default     = 50
}

variable "budget_emails" {
  description = "Correos de las alertas del presupuesto. Vacío = no se crea el presupuesto."
  type        = list(string)
  default     = []
}

variable "name" {
  description = "Prefijo de los nombres."
  type        = string
}

variable "vpc_id" {
  description = "VPC donde vive el ALB."
  type        = string
}

variable "vpc_cidr_block" {
  description = "CIDR de la VPC; el ALB solo puede salir hacia ahí."
  type        = string
}

variable "subnet_ids" {
  description = "Subredes públicas (al menos 2 zonas)."
  type        = list(string)

  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "El ALB necesita subredes en al menos 2 zonas."
  }
}

variable "api_port" {
  description = "Puerto del contenedor de la api."
  type        = number
  default     = 8000
}

variable "health_check_path" {
  description = "Ruta del health check de la api."
  type        = string
  default     = "/health"
}

variable "api_path_patterns" {
  description = "Rutas que el ALB envía a la api."
  type        = list(string)
  default     = ["/api/*"]
}

variable "certificate_arn" {
  description = "ARN de un certificado de ACM. Si es null solo hay listener HTTP."
  type        = string
  default     = null
}

variable "idle_timeout" {
  description = "Segundos de inactividad antes de cerrar la conexión (SSE o websockets)."
  type        = number
  default     = 120
}

variable "deletion_protection" {
  description = "Protección contra borrado del ALB (true en prod)."
  type        = bool
  default     = false
}

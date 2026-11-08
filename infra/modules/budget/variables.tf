variable "name" {
  description = "Nombre del presupuesto."
  type        = string
}

variable "limit_usd" {
  description = "Límite mensual en USD."
  type        = number
  default     = 50
}

variable "actual_thresholds_percent" {
  description = "Umbrales (en % del límite) del gasto real que disparan un correo."
  type        = list(number)
  default     = [50, 80, 100]
}

variable "emails" {
  description = "Correos que reciben las alertas."
  type        = list(string)

  validation {
    condition     = length(var.emails) > 0
    error_message = "Hace falta al menos un correo para las alertas del presupuesto."
  }
}

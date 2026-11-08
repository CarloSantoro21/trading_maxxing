variable "name" {
  description = "Prefijo del bucket; se le agrega -artifacts-<cuenta>."
  type        = string
}

variable "force_destroy" {
  description = "Permite borrar el bucket con objetos dentro (true en staging)."
  type        = bool
  default     = false
}

variable "noncurrent_version_days" {
  description = "Días que se guardan las versiones reemplazadas de un objeto."
  type        = number
  default     = 30
}

variable "name" {
  description = "Prefijo de los nombres (por ejemplo, trading-maxxing-prod)."
  type        = string
}

variable "cidr_block" {
  description = "Bloque CIDR de la VPC. Las subredes son /24 dentro de él."
  type        = string
  default     = "10.20.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.cidr_block))
    error_message = "cidr_block debe ser un CIDR IPv4 válido."
  }
}

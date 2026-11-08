variable "name_prefix" {
  description = "Prefijo de los nombres, por ejemplo trading-maxxing/prod."
  type        = string
}

variable "secrets" {
  description = "Secretos a crear: nombre => descripción y llaves JSON que debe contener."
  type = map(object({
    description = string
    keys        = list(string)
  }))
  default = {
    binance-testnet = {
      description = "Llaves de Binance Spot Testnet (testnet.binance.vision)"
      keys        = ["api_key", "api_secret"]
    }
    supabase = {
      description = "Proyecto de Supabase: URL y llaves"
      keys        = ["url", "anon_key", "service_role_key", "jwt_secret"]
    }
    groq = {
      description = "Llave de la API de Groq (LLM)"
      keys        = ["api_key"]
    }
    cryptopanic = {
      description = "Token de la API de CryptoPanic (noticias)"
      keys        = ["token"]
    }
  }
}

variable "recovery_window_in_days" {
  description = "Días antes de borrar un secreto eliminado (0 = inmediato, útil en staging)."
  type        = number
  default     = 7

  validation {
    condition     = var.recovery_window_in_days == 0 || (var.recovery_window_in_days >= 7 && var.recovery_window_in_days <= 30)
    error_message = "recovery_window_in_days debe ser 0 o estar entre 7 y 30."
  }
}

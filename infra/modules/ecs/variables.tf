variable "name" {
  description = "Nombre del cluster y prefijo de los recursos (por ejemplo, trading-maxxing-prod)."
  type        = string
}

variable "environment" {
  description = "Entorno (staging o prod)."
  type        = string
}

# --- Red ---------------------------------------------------------------------------------

variable "vpc_id" {
  description = "VPC de los servicios."
  type        = string
}

variable "subnet_ids" {
  description = "Subredes de las tareas (públicas, porque no hay NAT Gateway)."
  type        = list(string)
}

variable "assign_public_ip" {
  description = "IP pública por tarea; necesaria para salir a internet sin NAT Gateway."
  type        = bool
  default     = true
}

variable "alb_security_group_id" {
  description = "Security group del ALB, único origen permitido hacia la api."
  type        = string
}

variable "api_target_group_arn" {
  description = "Target group del ALB para la api."
  type        = string
}

variable "api_listener_rule_arn" {
  description = "Regla /api/* del ALB (solo para ordenar la creación)."
  type        = string
}

# --- Imágenes ----------------------------------------------------------------------------

variable "image_registry" {
  description = "Usuario u organización de Docker Hub que publica trading-maxxing-*."
  type        = string
}

variable "image_tag" {
  description = "Tag de las imágenes (develop, sha-<commit> o X.Y.Z)."
  type        = string
}

variable "redis_image" {
  description = "Imagen de Redis (multi-arquitectura, con arm64)."
  type        = string
  default     = "redis:7.4-alpine"
}

variable "dockerhub_credentials_secret_arn" {
  description = "Secreto con {\"username\",\"password\"} de Docker Hub para evitar el límite de pulls anónimos. Opcional."
  type        = string
  default     = null
}

# --- Tamaños y réplicas ------------------------------------------------------------------

variable "default_service_sizes" {
  description = "Tamaño de cada tarea según la propuesta (CPU en unidades: 1024 = 1 vCPU; memoria en MiB)."
  type        = map(object({ cpu = number, memory = number }))
  default = {
    api      = { cpu = 256, memory = 512 }
    agents   = { cpu = 512, memory = 1024 }
    engine   = { cpu = 512, memory = 1024 }
    forecast = { cpu = 1024, memory = 3072 }
    redis    = { cpu = 256, memory = 512 }
  }
}

variable "service_sizes" {
  description = "Sobrescribe el tamaño de algunos servicios."
  type        = map(object({ cpu = number, memory = number }))
  default     = {}
}

variable "desired_counts" {
  description = "Réplicas por servicio (por defecto 1). 0 apaga el servicio sin destruirlo."
  type        = map(number)
  default     = {}
}

variable "container_insights" {
  description = "Container Insights de CloudWatch (cuesta aparte; apagado por defecto)."
  type        = bool
  default     = false
}

# --- Configuración de la app -------------------------------------------------------------

variable "secret_arns" {
  description = "ARN de los secretos: binance-testnet, supabase, groq y cryptopanic."
  type        = map(string)

  validation {
    condition     = alltrue([for k in ["binance-testnet", "supabase", "groq", "cryptopanic"] : contains(keys(var.secret_arns), k)])
    error_message = "secret_arns debe incluir binance-testnet, supabase, groq y cryptopanic."
  }
}

variable "log_group_names" {
  description = "Log group de cada servicio (api, agents, engine, forecast y redis)."
  type        = map(string)
}

variable "artifacts_bucket_arn" {
  description = "ARN del bucket de artefactos."
  type        = string
}

variable "artifacts_bucket_name" {
  description = "Nombre del bucket de artefactos (se pasa como ARTIFACTS_BUCKET)."
  type        = string
}

variable "metric_namespace" {
  description = "Namespace de CloudWatch donde engine publica sus métricas."
  type        = string
  default     = "TradingMaxxing"
}

variable "log_level" {
  description = "LOG_LEVEL de los servicios."
  type        = string
  default     = "INFO"
}

variable "engine_symbols" {
  description = "ENGINE_SYMBOLS: pares que opera engine, separados por coma."
  type        = string
  default     = "BTCUSDT,ETHUSDT"
}

variable "engine_timeframe" {
  description = "ENGINE_TIMEFRAME de engine."
  type        = string
  default     = "1m"
}

variable "engine_profile" {
  description = "ENGINE_PROFILE de engine."
  type        = string
  default     = "default"
}

variable "engine_bot_id" {
  description = "ENGINE_BOT_ID: bot de Supabase al que se atribuyen las operaciones (opcional)."
  type        = string
  default     = ""
}

variable "extra_environment" {
  description = "Variables de entorno adicionales por servicio: { servicio = { NOMBRE = valor } }."
  type        = map(map(string))
  default     = {}
}

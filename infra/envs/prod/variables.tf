# Idéntico en envs/staging y envs/prod.

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

variable "environment" {
  description = "Nombre del entorno (staging o prod)."
  type        = string

  validation {
    condition     = contains(["staging", "prod"], var.environment)
    error_message = "environment debe ser staging o prod."
  }
}

variable "vpc_cidr" {
  description = "CIDR de la VPC del entorno."
  type        = string
}

# --- Imágenes ----------------------------------------------------------------------------

variable "dockerhub_username" {
  description = "Usuario de Docker Hub que publica trading-maxxing-* (el mismo secret DOCKERHUB_USERNAME del repo)."
  type        = string
}

variable "image_tag" {
  description = "Tag de las imágenes a desplegar (develop, sha-<commit> o X.Y.Z)."
  type        = string
  default     = "develop"
}

variable "dockerhub_credentials_secret_arn" {
  description = "Secreto opcional con credenciales de Docker Hub para el pull."
  type        = string
  default     = null
}

# --- Tamaños -----------------------------------------------------------------------------

variable "service_sizes" {
  description = "Sobrescribe CPU/memoria por servicio (ver modules/ecs)."
  type        = map(object({ cpu = number, memory = number }))
  default     = {}
}

variable "desired_counts" {
  description = "Réplicas por servicio; 0 apaga un servicio sin destruirlo."
  type        = map(number)
  default     = {}
}

# --- ALB ---------------------------------------------------------------------------------

variable "certificate_arn" {
  description = "Certificado de ACM para HTTPS. null = solo HTTP."
  type        = string
  default     = null
}

variable "alb_deletion_protection" {
  description = "Protección contra borrado del ALB."
  type        = bool
  default     = false
}

# --- Datos -------------------------------------------------------------------------------

variable "artifacts_force_destroy" {
  description = "Permite que terraform destroy borre el bucket de artefactos con contenido."
  type        = bool
  default     = false
}

variable "secrets_recovery_window_days" {
  description = "Días de recuperación de un secreto borrado (0 = inmediato)."
  type        = number
  default     = 7
}

# --- Observabilidad ----------------------------------------------------------------------

variable "log_retention_days" {
  description = "Retención de los logs en CloudWatch."
  type        = number
  default     = 14
}

variable "alarm_emails" {
  description = "Correos que reciben las alarmas (deben confirmar la suscripción)."
  type        = list(string)
  default     = []
}

variable "alarm_actions_enabled" {
  description = "Si las alarmas mandan correo."
  type        = bool
  default     = true
}

variable "drawdown_alarm_threshold" {
  description = "Drawdown (fracción) que dispara la alarma."
  type        = number
  default     = 0.10
}

variable "metric_namespace" {
  description = "Namespace de las métricas custom de engine."
  type        = string
  default     = "TradingMaxxing"
}

# --- App ---------------------------------------------------------------------------------

variable "log_level" {
  description = "LOG_LEVEL de los servicios."
  type        = string
  default     = "INFO"
}

variable "engine_symbols" {
  description = "Pares que opera engine."
  type        = string
  default     = "BTCUSDT,ETHUSDT"
}

variable "engine_timeframe" {
  description = "Timeframe de engine."
  type        = string
  default     = "1m"
}

variable "engine_bot_id" {
  description = "Bot de Supabase al que se atribuyen las operaciones (opcional)."
  type        = string
  default     = ""
}

# --- AWS Academy -------------------------------------------------------------------------

variable "existing_role_arn" {
  description = "AWS Academy no permite crear roles: ARN de LabRole para las tareas. null = se crean roles mínimos."
  type        = string
  default     = null
}

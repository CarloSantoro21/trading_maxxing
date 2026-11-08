variable "name" {
  description = "Prefijo de los nombres (alarmas y tema SNS)."
  type        = string
}

variable "environment" {
  description = "Entorno (staging o prod); es la dimensión Environment de las métricas custom."
  type        = string
}

variable "services" {
  description = "Servicios de ECS a vigilar; también define un log group por servicio."
  type        = list(string)
}

variable "log_group_prefix" {
  description = "Prefijo de los log groups, por ejemplo /ecs/trading-maxxing/prod."
  type        = string
}

variable "log_retention_days" {
  description = "Días de retención de los logs."
  type        = number
  default     = 14
}

variable "cluster_name" {
  description = "Nombre del cluster de ECS (dimensión ClusterName)."
  type        = string
}

variable "alb_arn_suffix" {
  description = "Sufijo del ARN del ALB (dimensión LoadBalancer)."
  type        = string
}

variable "api_target_group_arn_suffix" {
  description = "Sufijo del ARN del target group de la api (dimensión TargetGroup)."
  type        = string
}

variable "alarm_emails" {
  description = "Correos suscritos al tema SNS de alertas. Vacío = sin suscripciones."
  type        = list(string)
  default     = []
}

variable "actions_enabled" {
  description = "Si las alarmas notifican. En staging apagado puede evitar ruido con desired_count = 0."
  type        = bool
  default     = true
}

variable "service_down_minutes" {
  description = "Minutos seguidos sin tareas para considerar caído un servicio."
  type        = number
  default     = 5
}

variable "alb_5xx_threshold" {
  description = "Número de 5xx en 5 minutos que dispara la alarma."
  type        = number
  default     = 5
}

variable "metric_namespace" {
  description = "Namespace de las métricas custom que publica engine."
  type        = string
  default     = "TradingMaxxing"
}

variable "drawdown_threshold" {
  description = "Drawdown (fracción, 0.10 = 10 %) que dispara la alarma."
  type        = number
  default     = 0.10

  validation {
    condition     = var.drawdown_threshold > 0 && var.drawdown_threshold < 1
    error_message = "drawdown_threshold debe estar entre 0 y 1."
  }
}

variable "kill_switch_log_services" {
  description = "Servicios cuyos logs se revisan buscando el texto \"kill switch\"."
  type        = list(string)
  default     = ["engine", "agents"]
}

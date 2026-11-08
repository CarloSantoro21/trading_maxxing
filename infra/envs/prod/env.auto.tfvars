# Valores de prod (versionados, sin secretos). Corre 24/7 durante la demo.
environment = "prod"
vpc_cidr    = "10.20.0.0/16"

alb_deletion_protection      = true
artifacts_force_destroy      = false
secrets_recovery_window_days = 7
log_retention_days           = 14
alarm_actions_enabled        = true
drawdown_alarm_threshold     = 0.10

# Tamaños de la propuesta (los valores por defecto de modules/ecs):
# api 0.25 vCPU / 0.5 GB · agents y engine 0.5 / 1 GB · forecast 1 / 3 GB · redis 0.25 / 0.5 GB
service_sizes  = {}
desired_counts = {}

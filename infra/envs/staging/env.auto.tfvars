# Valores de staging (versionados, sin secretos). Se levanta bajo demanda y se destruye
# al terminar (`terraform destroy`), por eso todo se puede borrar sin trámites.
environment = "staging"
vpc_cidr    = "10.30.0.0/16"

alb_deletion_protection      = false
artifacts_force_destroy      = true
secrets_recovery_window_days = 0
log_retention_days           = 3
alarm_actions_enabled        = true
drawdown_alarm_threshold     = 0.10

# Mismos tamaños que prod para que staging sea representativo. Para ahorrar mientras no se
# usa forecast: desired_counts = { forecast = 0 }
service_sizes  = {}
desired_counts = {}

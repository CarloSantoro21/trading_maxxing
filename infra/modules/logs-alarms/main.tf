# Observabilidad (#29): log groups de cada servicio, tema SNS con suscripción por correo
# y las alarmas de CloudWatch.
#
# Alarmas:
#   1. <servicio>-down (una por servicio): el servicio de ECS no publica CPUUtilization, es
#      decir, no tiene tareas corriendo. Se usa SampleCount con datos faltantes = "breaching"
#      porque RunningTaskCount solo existe con Container Insights (que cuesta aparte).
#   2. alb-5xx: suma de 5xx del ALB y de la api por encima del umbral.
#   3. api-no-healthy-targets: el target group de la api se quedó sin targets sanos.
#   4. drawdown: métrica custom que publica engine (ver el contrato en infra/README.md).
#   5. kill-switch: el kill switch de riesgo se activó (métrica de engine o línea de log).

locals {
  alarm_actions = [aws_sns_topic.alerts.arn]
  dimensions    = { Environment = var.environment }

  log_metric_namespace = "${var.metric_namespace}/${var.environment}"
}

# --- Logs --------------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "service" {
  for_each = toset(var.services)

  name              = "${var.log_group_prefix}/${each.key}"
  retention_in_days = var.log_retention_days

  tags = { Service = each.key }
}

# --- Notificaciones ----------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name = "${var.name}-alerts"
}

# Cada correo debe confirmar la suscripción desde el mensaje que manda AWS.
resource "aws_sns_topic_subscription" "email" {
  for_each = toset(var.alarm_emails)

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.key
}

# --- 1. Servicio caído --------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "service_down" {
  for_each = toset(var.services)

  alarm_name        = "${var.name}-${each.key}-down"
  alarm_description = "El servicio ${each.key} no tiene tareas corriendo en ${var.cluster_name}."
  actions_enabled   = var.actions_enabled

  namespace   = "AWS/ECS"
  metric_name = "CPUUtilization"
  dimensions = {
    ClusterName = var.cluster_name
    ServiceName = each.key
  }
  statistic           = "SampleCount"
  period              = 60
  evaluation_periods  = var.service_down_minutes
  datapoints_to_alarm = var.service_down_minutes
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}

# --- 2. Errores 5xx del ALB ---------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name        = "${var.name}-alb-5xx"
  alarm_description = "Más de ${var.alb_5xx_threshold} respuestas 5xx (ALB + api) en 5 minutos."
  actions_enabled   = var.actions_enabled

  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = var.alb_5xx_threshold
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "total"
    expression  = "FILL(elb, 0) + FILL(target, 0)"
    label       = "5xx totales"
    return_data = true
  }

  metric_query {
    id = "elb"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_ELB_5XX_Count"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
      period      = 300
      stat        = "Sum"
    }
  }

  metric_query {
    id = "target"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_Target_5XX_Count"
      dimensions  = { LoadBalancer = var.alb_arn_suffix }
      period      = 300
      stat        = "Sum"
    }
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}

# --- 3. Sin targets sanos -----------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "api_no_healthy_targets" {
  alarm_name        = "${var.name}-api-no-healthy-targets"
  alarm_description = "El ALB no tiene ninguna tarea sana de la api."
  actions_enabled   = var.actions_enabled

  namespace   = "AWS/ApplicationELB"
  metric_name = "HealthyHostCount"
  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.api_target_group_arn_suffix
  }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}

# --- 4. Drawdown (métrica custom de engine) -----------------------------------------------

resource "aws_cloudwatch_metric_alarm" "drawdown" {
  alarm_name        = "${var.name}-drawdown"
  alarm_description = "El drawdown reportado por engine superó ${var.drawdown_threshold * 100} %."
  actions_enabled   = var.actions_enabled

  namespace           = var.metric_namespace
  metric_name         = "Drawdown"
  dimensions          = local.dimensions
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = var.drawdown_threshold
  # Sin datos (engine detenido) no es drawdown: eso ya lo cubre la alarma engine-down.
  treat_missing_data = "notBreaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}

# --- 5. Kill switch -----------------------------------------------------------------------

# Respaldo: si engine o agents escriben "kill switch" en su log, se cuenta como activación
# aunque engine todavía no publique la métrica directamente. Un filtro con patrón de texto no
# puede poner dimensiones, por eso esta métrica va a un namespace propio de cada entorno.
resource "aws_cloudwatch_log_metric_filter" "kill_switch" {
  for_each = toset([for s in var.kill_switch_log_services : s if contains(var.services, s)])

  name           = "${var.name}-${each.key}-kill-switch"
  log_group_name = aws_cloudwatch_log_group.service[each.key].name
  pattern        = "\"kill switch\""

  metric_transformation {
    namespace     = local.log_metric_namespace
    name          = "KillSwitchLogged"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "kill_switch" {
  alarm_name        = "${var.name}-kill-switch"
  alarm_description = "Se activó el kill switch de riesgo: un bot cerró posiciones y se detuvo."
  actions_enabled   = var.actions_enabled

  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "total"
    expression  = "FILL(published, 0) + FILL(logged, 0)"
    label       = "Activaciones del kill switch"
    return_data = true
  }

  # Publicada por engine con la dimensión Environment.
  metric_query {
    id = "published"
    metric {
      namespace   = var.metric_namespace
      metric_name = "KillSwitchTriggered"
      dimensions  = local.dimensions
      period      = 60
      stat        = "Sum"
    }
  }

  # Derivada de los logs (filtro de arriba).
  metric_query {
    id = "logged"
    metric {
      namespace   = local.log_metric_namespace
      metric_name = "KillSwitchLogged"
      period      = 60
      stat        = "Sum"
    }
  }

  alarm_actions = local.alarm_actions
}

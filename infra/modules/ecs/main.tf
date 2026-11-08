# Cluster de ECS Fargate en ARM64 (Graviton) con un servicio por contenedor:
#   api       FastAPI detrás del ALB (puerto 8000)
#   agents    estrategias, riesgo, noticias y LLM (worker, sin puerto)
#   engine    NautilusTrader contra Binance Testnet (worker, sin puerto)
#   forecast  Kronos en CPU (puerto 8001, solo dentro del namespace)
#   redis     redis:7.4-alpine como servicio propio (puerto 6379, solo dentro del namespace)
#
# Redis corre como tarea de Fargate en lugar de ElastiCache: un cache.t4g.micro cuesta
# ~11.7 USD/mes y una tarea de 0.25 vCPU / 0.5 GB ~7.2 USD/mes, sin cambiar el código. La
# contra es que no persiste: si la tarea se reinicia se pierden los streams pendientes, lo
# cual es aceptable porque la fuente de verdad es Supabase.
#
# Los servicios se encuentran entre sí con ECS Service Connect (namespace HTTP de Cloud
# Map): redis://redis:6379 y http://forecast:8001, los mismos nombres que en docker-compose.

data "aws_region" "current" {}

locals {
  region = data.aws_region.current.region

  image = { for s in ["api", "agents", "engine", "forecast"] :
    s => "${var.image_registry}/trading-maxxing-${s}:${var.image_tag}"
  }

  sizes = merge(var.default_service_sizes, var.service_sizes)

  desired = merge(
    { api = 1, agents = 1, engine = 1, forecast = 1, redis = 1 },
    var.desired_counts,
  )

  # valueFrom de un campo de un secreto JSON: <arn>:<llave>:: (versión actual).
  secret = {
    binance_key        = "${var.secret_arns["binance-testnet"]}:api_key::"
    binance_secret     = "${var.secret_arns["binance-testnet"]}:api_secret::"
    supabase_url       = "${var.secret_arns["supabase"]}:url::"
    supabase_anon      = "${var.secret_arns["supabase"]}:anon_key::"
    supabase_service   = "${var.secret_arns["supabase"]}:service_role_key::"
    supabase_jwt       = "${var.secret_arns["supabase"]}:jwt_secret::"
    groq_api_key       = "${var.secret_arns["groq"]}:api_key::"
    cryptopanic_token  = "${var.secret_arns["cryptopanic"]}:token::"
    dockerhub_password = var.dockerhub_credentials_secret_arn
  }

  common_env = {
    ENVIRONMENT       = var.environment
    LOG_LEVEL         = var.log_level
    REDIS_URL         = "redis://redis:6379/0"
    FORECAST_URL      = "http://forecast:8001"
    AWS_REGION        = local.region
    ARTIFACTS_BUCKET  = var.artifacts_bucket_name
    METRICS_NAMESPACE = var.metric_namespace
  }

  heartbeat_check = ["CMD", "python", "-c", "import os,sys,time; sys.exit(time.time() - os.path.getmtime('/tmp/heartbeat') > 60)"]

  # Especificación de cada servicio. ports: puertos que expone; sc_server: si se publica en
  # Service Connect con ese nombre; single: a lo más una tarea a la vez (engine no debe
  # duplicar órdenes y redis no se puede replicar así).
  services = {
    api = {
      image   = local.image["api"]
      command = null
      port    = 8000
      single  = false
      env     = {}
      secrets = {
        SUPABASE_URL              = local.secret.supabase_url
        SUPABASE_ANON_KEY         = local.secret.supabase_anon
        SUPABASE_SERVICE_ROLE_KEY = local.secret.supabase_service
        SUPABASE_JWT_SECRET       = local.secret.supabase_jwt
      }
      health = {
        command     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]
        startPeriod = 30
      }
      sc_server = false
    }
    agents = {
      image   = local.image["agents"]
      command = null
      port    = null
      single  = true
      env     = { LLM_PROVIDER = "groq" }
      secrets = {
        SUPABASE_URL              = local.secret.supabase_url
        SUPABASE_SERVICE_ROLE_KEY = local.secret.supabase_service
        GROQ_API_KEY              = local.secret.groq_api_key
        CRYPTOPANIC_TOKEN         = local.secret.cryptopanic_token
      }
      health    = { command = local.heartbeat_check, startPeriod = 30 }
      sc_server = false
    }
    engine = {
      image   = local.image["engine"]
      command = null
      port    = null
      single  = true
      env = {
        ENGINE_SYMBOLS   = var.engine_symbols
        ENGINE_TIMEFRAME = var.engine_timeframe
        ENGINE_PROFILE   = var.engine_profile
        ENGINE_BOT_ID    = var.engine_bot_id
      }
      secrets = {
        BINANCE_TESTNET_API_KEY    = local.secret.binance_key
        BINANCE_TESTNET_API_SECRET = local.secret.binance_secret
        SUPABASE_URL               = local.secret.supabase_url
        SUPABASE_SERVICE_ROLE_KEY  = local.secret.supabase_service
      }
      health    = { command = local.heartbeat_check, startPeriod = 60 }
      sc_server = false
    }
    forecast = {
      image   = local.image["forecast"]
      command = null
      port    = 8001
      single  = false
      env = {
        FORECAST_DEVICE = "cpu"
        HF_HOME         = "/tmp/huggingface" # el usuario sin privilegios no puede escribir en /app
      }
      secrets = {}
      health = {
        command     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=4)"]
        startPeriod = 300 # descarga y carga de Kronos
      }
      sc_server = true
    }
    redis = {
      image = var.redis_image
      # Sin persistencia: el disco de Fargate es efímero y Supabase es la fuente de verdad.
      command   = ["redis-server", "--save", "", "--appendonly", "no", "--maxmemory-policy", "noeviction"]
      port      = 6379
      single    = true
      env       = {}
      secrets   = {}
      health    = { command = ["CMD", "redis-cli", "ping"], startPeriod = 10 }
      sc_server = true
    }
  }

  # Los secretos de Docker Hub son opcionales (sin ellos se hace pull anónimo).
  repository_credentials = var.dockerhub_credentials_secret_arn == null ? null : {
    credentialsParameter = var.dockerhub_credentials_secret_arn
  }

  execution_secret_arns = compact(concat(
    values(var.secret_arns),
    [var.dockerhub_credentials_secret_arn],
  ))
}

# --- Cluster y namespace -----------------------------------------------------------------

resource "aws_ecs_cluster" "this" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = var.container_insights ? "enabled" : "disabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

resource "aws_service_discovery_http_namespace" "this" {
  name        = var.name
  description = "Service Connect de ${var.name}"
}

# --- Security groups ---------------------------------------------------------------------
# Ninguna regla de entrada viene de internet: solo el ALB entra, y solo al puerto 8000.
# La salida es 443 (Docker Hub, Binance, Supabase, Groq, CryptoPanic, Hugging Face y las
# APIs de AWS) más los puertos internos.

resource "aws_security_group" "app" {
  name        = "${var.name}-app"
  description = "api, agents, engine y forecast"
  vpc_id      = var.vpc_id

  tags = { Name = "${var.name}-app" }
}

resource "aws_security_group" "redis" {
  name        = "${var.name}-redis"
  description = "Redis: solo acepta conexiones de los servicios de la app"
  vpc_id      = var.vpc_id

  tags = { Name = "${var.name}-redis" }
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.app.id
  description                  = "ALB hacia la api"
  referenced_security_group_id = var.alb_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_ingress_rule" "forecast_from_app" {
  security_group_id            = aws_security_group.app.id
  description                  = "agents y engine hacia forecast"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 8001
  to_port                      = 8001
}

resource "aws_vpc_security_group_ingress_rule" "redis_from_app" {
  security_group_id            = aws_security_group.redis.id
  description                  = "Servicios de la app hacia redis"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
}

resource "aws_vpc_security_group_egress_rule" "app_https" {
  security_group_id = aws_security_group.app.id
  description       = "HTTPS saliente (imagenes, APIs externas y de AWS)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "app_to_forecast" {
  security_group_id            = aws_security_group.app.id
  description                  = "Hacia forecast"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 8001
  to_port                      = 8001
}

resource "aws_vpc_security_group_egress_rule" "app_to_redis" {
  security_group_id            = aws_security_group.app.id
  description                  = "Hacia redis"
  referenced_security_group_id = aws_security_group.redis.id
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
}

resource "aws_vpc_security_group_egress_rule" "redis_https" {
  security_group_id = aws_security_group.redis.id
  description       = "HTTPS saliente (pull de la imagen y agente de ECS)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# --- IAM ---------------------------------------------------------------------------------

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Con var.existing_role_arn (AWS Academy: LabRole) no se crea ningún rol de IAM y las
# tareas usan ese rol como rol de ejecución y de tarea.
locals {
  create_roles       = var.existing_role_arn == null
  execution_role_arn = local.create_roles ? aws_iam_role.execution[0].arn : var.existing_role_arn
  task_role_arns = local.create_roles ? { for k, r in aws_iam_role.task : k => r.arn } : {
    for k, _ in local.services : k => var.existing_role_arn
  }
}

# Rol de ejecución: lo usa el agente de ECS para hacer pull, escribir logs y leer secretos.
resource "aws_iam_role" "execution" {
  count = local.create_roles ? 1 : 0

  name               = "${var.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  count = local.create_roles ? 1 : 0

  role       = aws_iam_role.execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    sid       = "ReadAppSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = local.execution_secret_arns
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  count = local.create_roles ? 1 : 0

  name   = "read-secrets"
  role   = aws_iam_role.execution[0].id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

# Rol de tarea: lo que el código de cada servicio puede hacer en AWS. Uno por servicio.
resource "aws_iam_role" "task" {
  for_each = local.create_roles ? local.services : {}

  name               = "${var.name}-${each.key}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "task" {
  for_each = { for k, _ in local.services : k => k if k != "redis" && local.create_roles }

  # Lectura de artefactos (pesos, pronósticos precalculados y datos históricos).
  statement {
    sid       = "ListArtifacts"
    actions   = ["s3:ListBucket"]
    resources = [var.artifacts_bucket_arn]
  }

  statement {
    sid       = "ReadArtifacts"
    actions   = ["s3:GetObject"]
    resources = ["${var.artifacts_bucket_arn}/*"]
  }

  # agents escribe los reportes de backtest bajo reports/.
  dynamic "statement" {
    for_each = each.key == "agents" ? [1] : []
    content {
      sid       = "WriteReports"
      actions   = ["s3:PutObject"]
      resources = ["${var.artifacts_bucket_arn}/reports/*"]
    }
  }

  # engine publica Drawdown y KillSwitchTriggered, solo en su namespace.
  dynamic "statement" {
    for_each = each.key == "engine" ? [1] : []
    content {
      sid       = "PutTradingMetrics"
      actions   = ["cloudwatch:PutMetricData"]
      resources = ["*"] # PutMetricData no admite ARN de recurso; se acota con la condición

      condition {
        test     = "StringEquals"
        variable = "cloudwatch:namespace"
        values   = [var.metric_namespace]
      }
    }
  }
}

resource "aws_iam_role_policy" "task" {
  for_each = data.aws_iam_policy_document.task

  name   = "app"
  role   = aws_iam_role.task[each.key].id
  policy = each.value.json
}

# --- Task definitions --------------------------------------------------------------------

resource "aws_ecs_task_definition" "this" {
  for_each = local.services

  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = local.sizes[each.key].cpu
  memory                   = local.sizes[each.key].memory
  execution_role_arn       = local.execution_role_arn
  task_role_arn            = local.task_role_arns[each.key]

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([
    {
      name      = each.key
      image     = each.value.image
      essential = true
      command   = each.value.command

      repositoryCredentials = each.key == "redis" ? null : local.repository_credentials

      portMappings = each.value.port == null ? [] : [{
        name          = each.key
        containerPort = each.value.port
        protocol      = "tcp"
        appProtocol   = each.key == "redis" ? null : "http"
      }]

      environment = [
        for k, v in merge(local.common_env, each.value.env, lookup(var.extra_environment, each.key, {})) :
        { name = k, value = v } if v != null && v != ""
      ]

      secrets = [for k, v in each.value.secrets : { name = k, valueFrom = v }]

      healthCheck = {
        command     = each.value.health.command
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = each.value.health.startPeriod
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = var.log_group_names[each.key]
          awslogs-region        = local.region
          awslogs-stream-prefix = each.key
        }
      }
    }
  ])
}

# --- Servicios ---------------------------------------------------------------------------

resource "aws_ecs_service" "this" {
  for_each = local.services

  name             = each.key
  cluster          = aws_ecs_cluster.this.id
  task_definition  = aws_ecs_task_definition.this[each.key].arn
  desired_count    = local.desired[each.key]
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  # Servicios "single": se detiene la tarea vieja antes de arrancar la nueva para que nunca
  # haya dos engines mandando órdenes ni dos redis con datos distintos.
  deployment_minimum_healthy_percent = each.value.single ? 0 : 100
  deployment_maximum_percent         = each.value.single ? 100 : 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  enable_execute_command = false
  propagate_tags         = "SERVICE"
  wait_for_steady_state  = false

  # Sin NAT Gateway: la tarea necesita IP pública para salir a internet. El security group
  # no tiene reglas de entrada públicas.
  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [each.key == "redis" ? aws_security_group.redis.id : aws_security_group.app.id]
    assign_public_ip = var.assign_public_ip
  }

  dynamic "load_balancer" {
    for_each = each.key == "api" ? [1] : []
    content {
      target_group_arn = var.api_target_group_arn
      container_name   = "api"
      container_port   = 8000
    }
  }

  health_check_grace_period_seconds = each.key == "api" ? 60 : null

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn

    dynamic "service" {
      for_each = each.value.sc_server ? [1] : []
      content {
        port_name      = each.key
        discovery_name = each.key

        client_alias {
          dns_name = each.key
          port     = each.value.port
        }
      }
    }
  }

  # La regla del ALB debe existir antes de registrar la api en el target group; la etiqueta
  # crea esa dependencia sin un depends_on a nivel de módulo.
  tags = each.key == "api" ? { AlbListenerRule = var.api_listener_rule_arn } : {}

  lifecycle {
    # Permite escalar a mano (o con autoscaling) sin que el siguiente plan lo revierta.
    ignore_changes = [desired_count]
  }
}

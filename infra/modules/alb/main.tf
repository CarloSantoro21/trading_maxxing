# Application Load Balancer público. Solo la api queda detrás de él: el frontend se sirve
# desde Cloudflare Pages (#45) y agents, engine, forecast y redis no reciben tráfico externo.
#
# Reglas:
#   /api/*  -> target group de la api (puerto 8000, health check en /health)
#   resto   -> 404 fijo
# Si se pasa certificate_arn se crea el listener HTTPS y el de HTTP redirige a 443.

locals {
  https_enabled = var.certificate_arn != null
}

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "ALB de ${var.name}: HTTP/HTTPS desde internet, salida solo a la api"
  vpc_id      = var.vpc_id

  tags = { Name = "${var.name}-alb" }
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP publico"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  count = local.https_enabled ? 1 : 0

  security_group_id = aws_security_group.alb.id
  description       = "HTTPS publico"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# Salida solo al puerto de la api dentro de la VPC (tráfico a los targets y health checks).
resource "aws_vpc_security_group_egress_rule" "to_api" {
  security_group_id = aws_security_group.alb.id
  description       = "Hacia las tareas de la api"
  cidr_ipv4         = var.vpc_cidr_block
  ip_protocol       = "tcp"
  from_port         = var.api_port
  to_port           = var.api_port
}

resource "aws_lb" "this" {
  name               = substr("${var.name}-alb", 0, 32)
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.subnet_ids
  idle_timeout       = var.idle_timeout

  drop_invalid_header_fields = true
  enable_deletion_protection = var.deletion_protection

  tags = { Name = "${var.name}-alb" }
}

resource "aws_lb_target_group" "api" {
  name        = substr("${var.name}-api", 0, 32)
  port        = var.api_port
  protocol    = "HTTP"
  target_type = "ip" # Fargate (awsvpc) registra IPs, no instancias
  vpc_id      = var.vpc_id

  deregistration_delay = 30

  health_check {
    path                = var.health_check_path
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = { Name = "${var.name}-api" }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = local.https_enabled ? "redirect" : "fixed-response"

    dynamic "redirect" {
      for_each = local.https_enabled ? [1] : []
      content {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }

    dynamic "fixed_response" {
      for_each = local.https_enabled ? [] : [1]
      content {
        content_type = "application/json"
        message_body = "{\"detail\":\"Not Found\"}"
        status_code  = "404"
      }
    }
  }
}

resource "aws_lb_listener" "https" {
  count = local.https_enabled ? 1 : 0

  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "application/json"
      message_body = "{\"detail\":\"Not Found\"}"
      status_code  = "404"
    }
  }
}

# La regla de la api cuelga del listener que recibe el tráfico real (HTTPS si existe).
resource "aws_lb_listener_rule" "api" {
  listener_arn = local.https_enabled ? aws_lb_listener.https[0].arn : aws_lb_listener.http.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    path_pattern {
      values = var.api_path_patterns
    }
  }
}

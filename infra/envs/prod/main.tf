# Entorno completo de trading_maxxing. Este archivo es idéntico en envs/staging y envs/prod:
# lo que cambia entre entornos vive en env.auto.tfvars (versionado) y en terraform.tfvars
# (local, con correos y el usuario de Docker Hub) o en variables TF_VAR_* del CI.

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
      Repo        = "CarloSantoro21/trading_maxxing"
    }
  }
}

locals {
  name     = "${var.project}-${var.environment}"
  services = ["api", "agents", "engine", "forecast", "redis"]
}

module "network" {
  source = "../../modules/network"

  name       = local.name
  cidr_block = var.vpc_cidr
}

module "s3" {
  source = "../../modules/s3"

  name          = local.name
  force_destroy = var.artifacts_force_destroy
}

module "secrets" {
  source = "../../modules/secrets"

  name_prefix             = "${var.project}/${var.environment}"
  recovery_window_in_days = var.secrets_recovery_window_days
}

module "alb" {
  source = "../../modules/alb"

  name                = local.name
  vpc_id              = module.network.vpc_id
  vpc_cidr_block      = module.network.vpc_cidr_block
  subnet_ids          = module.network.public_subnet_ids
  certificate_arn     = var.certificate_arn
  deletion_protection = var.alb_deletion_protection
}

module "logs_alarms" {
  source = "../../modules/logs-alarms"

  name                        = local.name
  environment                 = var.environment
  services                    = local.services
  log_group_prefix            = "/ecs/${var.project}/${var.environment}"
  log_retention_days          = var.log_retention_days
  cluster_name                = local.name
  alb_arn_suffix              = module.alb.arn_suffix
  api_target_group_arn_suffix = module.alb.api_target_group_arn_suffix
  alarm_emails                = var.alarm_emails
  actions_enabled             = var.alarm_actions_enabled
  drawdown_threshold          = var.drawdown_alarm_threshold
  metric_namespace            = var.metric_namespace
}

module "ecs" {
  source = "../../modules/ecs"

  name        = local.name
  environment = var.environment

  vpc_id                = module.network.vpc_id
  subnet_ids            = module.network.public_subnet_ids
  alb_security_group_id = module.alb.security_group_id
  api_target_group_arn  = module.alb.api_target_group_arn
  api_listener_rule_arn = module.alb.listener_rule_arn

  image_registry                   = var.dockerhub_username
  image_tag                        = var.image_tag
  dockerhub_credentials_secret_arn = var.dockerhub_credentials_secret_arn

  service_sizes  = var.service_sizes
  desired_counts = var.desired_counts

  existing_role_arn = var.existing_role_arn

  secret_arns           = module.secrets.arns
  log_group_names       = module.logs_alarms.log_group_names
  artifacts_bucket_arn  = module.s3.bucket_arn
  artifacts_bucket_name = module.s3.bucket_name
  metric_namespace      = var.metric_namespace

  log_level        = var.log_level
  engine_symbols   = var.engine_symbols
  engine_timeframe = var.engine_timeframe
  engine_bot_id    = var.engine_bot_id
}

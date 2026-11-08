# Bootstrap de la cuenta (#39). Se aplica UNA vez, con estado local, antes que los entornos:
#   - bucket S3 del estado de Terraform (versionado, cifrado y privado)
#   - tabla DynamoDB para el lock del estado
#   - AWS Budget con alerta de 50 USD (es de la cuenta, no de un entorno)
#   - proveedor OIDC y roles de GitHub Actions (opcional: AWS Academy no lo permite)
#
# Ver infra/README.md, sección "Bootstrap".

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0, < 7.0"
    }
  }

  # Estado local a propósito: este stack crea el backend que usan los demás. Si se quiere,
  # después de aplicarlo se puede migrar con un bloque backend "s3" y `terraform init -migrate-state`.
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      Stack     = "bootstrap"
      ManagedBy = "terraform"
      Repo      = var.github_repository
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  state_bucket = "${var.project}-tfstate-${data.aws_caller_identity.current.account_id}"
  lock_table   = "${var.project}-tflock"
}

# --- Estado ------------------------------------------------------------------------------

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

data "aws_iam_policy_document" "state_tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state_tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.state]
}

# On-demand: con unos cuantos locks al día cuesta prácticamente 0.
resource "aws_dynamodb_table" "lock" {
  name         = local.lock_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }

  server_side_encryption {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

# --- Presupuesto -------------------------------------------------------------------------

module "budget" {
  source = "../modules/budget"
  count  = length(var.budget_emails) > 0 ? 1 : 0

  name      = "${var.project}-monthly"
  limit_usd = var.budget_limit_usd
  emails    = var.budget_emails
}

# --- GitHub Actions (OIDC) ---------------------------------------------------------------

module "github_oidc" {
  source = "../modules/github-oidc"
  count  = var.enable_github_oidc ? 1 : 0

  github_repository          = var.github_repository
  resource_prefix            = var.project
  region                     = var.region
  create_oidc_provider       = var.existing_oidc_provider_arn == null
  existing_oidc_provider_arn = var.existing_oidc_provider_arn
  state_bucket_arn           = aws_s3_bucket.state.arn
  lock_table_arn             = aws_dynamodb_table.lock.arn
}

# Acceso de GitHub Actions a AWS con OIDC: sin llaves de larga duración (#39).
#
# Roles:
#   <prefix>-gha-plan          PRs y develop: solo lectura de la cuenta + lock del estado.
#   <prefix>-gha-apply-<env>   jobs con `environment: <env>` (aprobación manual en GitHub).
#
# IMPORTANTE (AWS Academy): el Learner Lab NO permite crear proveedores OIDC ni roles de
# IAM (solo existe LabRole). Ahí este módulo se desactiva (enable_github_oidc = false en el
# bootstrap) y el workflow usa las credenciales temporales del lab como secrets
# (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY y AWS_SESSION_TOKEN), que caducan con la sesión.
# Ver infra/README.md.

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  repo       = var.github_repository

  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : var.existing_oidc_provider_arn

  # Prefijos de los recursos que administra Terraform.
  p = var.resource_prefix
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # AWS ya no valida el thumbprint para GitHub, pero el API aún pide uno.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

# --- Confianza ---------------------------------------------------------------------------

data "aws_iam_policy_document" "trust_plan" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${local.repo}:pull_request",
        "repo:${local.repo}:ref:refs/heads/develop",
        "repo:${local.repo}:ref:refs/heads/main",
      ]
    }
  }
}

data "aws_iam_policy_document" "trust_apply" {
  for_each = toset(var.environments)

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Solo jobs que declaran `environment: <env>`, protegido con revisores en GitHub.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${local.repo}:environment:${each.key}"]
    }
  }
}

# --- Estado de Terraform (común a plan y apply) --------------------------------------------

data "aws_iam_policy_document" "state" {
  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = [var.state_bucket_arn]
  }

  statement {
    sid       = "StateReadWrite"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${var.state_bucket_arn}/*"]
  }

  statement {
    sid       = "StateLock"
    actions   = ["dynamodb:DescribeTable", "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [var.lock_table_arn]
  }
}

# --- Plan: solo lectura -------------------------------------------------------------------

data "aws_iam_policy_document" "plan" {
  source_policy_documents = [data.aws_iam_policy_document.state.json]

  statement {
    sid = "ReadInfra"
    actions = [
      "ec2:Describe*",
      "ecs:Describe*",
      "ecs:List*",
      "elasticloadbalancing:Describe*",
      "servicediscovery:Get*",
      "servicediscovery:List*",
      "logs:Describe*",
      "logs:ListTagsForResource",
      "logs:ListTagsLogGroup",
      "cloudwatch:Describe*",
      "cloudwatch:ListTagsForResource",
      "sns:GetTopicAttributes",
      "sns:GetSubscriptionAttributes",
      "sns:ListSubscriptionsByTopic",
      "sns:ListTagsForResource",
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetResourcePolicy",
      "secretsmanager:ListSecrets",
      "s3:GetBucket*",
      "s3:GetEncryptionConfiguration",
      "s3:GetLifecycleConfiguration",
      "s3:GetAccelerateConfiguration",
      "s3:GetReplicationConfiguration",
      "s3:ListBucket",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:GetOpenIDConnectProvider",
      "budgets:ViewBudget",
      "budgets:ListTagsForResource",
      "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }

  # El plan nunca lee valores de secretos.
  statement {
    sid       = "NoSecretValues"
    effect    = "Deny"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["*"]
  }
}

resource "aws_iam_role" "plan" {
  name                 = "${local.p}-gha-plan"
  description          = "GitHub Actions: terraform plan (solo lectura)"
  assume_role_policy   = data.aws_iam_policy_document.trust_plan.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "plan" {
  name   = "terraform-plan"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan.json
}

# --- Apply: administra solo los recursos del proyecto ------------------------------------

data "aws_iam_policy_document" "apply" {
  source_policy_documents = [data.aws_iam_policy_document.plan.json]

  # EC2, ECS, ELB y Cloud Map casi no admiten permisos por recurso al crear; se acotan a la
  # región del proyecto.
  statement {
    sid = "ManageComputeAndNetwork"
    actions = [
      "ec2:*Vpc*",
      "ec2:*Subnet*",
      "ec2:*InternetGateway*",
      "ec2:*RouteTable*",
      "ec2:*Route",
      "ec2:*SecurityGroup*",
      "ec2:CreateTags",
      "ec2:DeleteTags",
      "ecs:*",
      "elasticloadbalancing:*",
      "servicediscovery:*",
      "route53:CreateHostedZone",
      "route53:DeleteHostedZone",
      "route53:GetHostedZone",
      "route53:ChangeTagsForResource",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }

  statement {
    sid       = "ManageLogs"
    actions   = ["logs:*"]
    resources = ["arn:${local.partition}:logs:${var.region}:${local.account_id}:log-group:/ecs/${local.p}*"]
  }

  statement {
    sid       = "ManageAlarms"
    actions   = ["cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms", "cloudwatch:TagResource", "cloudwatch:UntagResource"]
    resources = ["arn:${local.partition}:cloudwatch:${var.region}:${local.account_id}:alarm:${local.p}*"]
  }

  statement {
    sid       = "ManageSns"
    actions   = ["sns:*"]
    resources = ["arn:${local.partition}:sns:${var.region}:${local.account_id}:${local.p}*"]
  }

  # Crea y borra los secretos, pero no puede leer ni escribir sus valores.
  statement {
    sid = "ManageSecrets"
    actions = [
      "secretsmanager:CreateSecret",
      "secretsmanager:DeleteSecret",
      "secretsmanager:UpdateSecret",
      "secretsmanager:TagResource",
      "secretsmanager:UntagResource",
      "secretsmanager:RestoreSecret",
    ]
    resources = ["arn:${local.partition}:secretsmanager:${var.region}:${local.account_id}:secret:${local.p}/*"]
  }

  statement {
    sid       = "ManageArtifactsBucket"
    actions   = ["s3:*"]
    resources = ["arn:${local.partition}:s3:::${local.p}*-artifacts-*", "arn:${local.partition}:s3:::${local.p}*-artifacts-*/*"]
  }

  statement {
    sid = "ManageTaskRoles"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:UpdateRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:UpdateAssumeRolePolicy",
    ]
    resources = ["arn:${local.partition}:iam::${local.account_id}:role/${local.p}-*"]
  }

  # Solo se pueden pasar los roles del proyecto, y solo a ECS.
  statement {
    sid       = "PassTaskRoles"
    actions   = ["iam:PassRole"]
    resources = ["arn:${local.partition}:iam::${local.account_id}:role/${local.p}-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid       = "ServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["ecs.amazonaws.com", "elasticloadbalancing.amazonaws.com"]
    }
  }

  # Los roles de GitHub no pueden modificarse a sí mismos.
  statement {
    sid    = "NoSelfEscalation"
    effect = "Deny"
    actions = [
      "iam:*",
    ]
    resources = ["arn:${local.partition}:iam::${local.account_id}:role/${local.p}-gha-*"]
  }
}

resource "aws_iam_role" "apply" {
  for_each = toset(var.environments)

  name                 = "${local.p}-gha-apply-${each.key}"
  description          = "GitHub Actions: terraform apply en ${each.key}"
  assume_role_policy   = data.aws_iam_policy_document.trust_apply[each.key].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "apply" {
  for_each = aws_iam_role.apply

  name   = "terraform-apply"
  role   = each.value.id
  policy = data.aws_iam_policy_document.apply.json
}

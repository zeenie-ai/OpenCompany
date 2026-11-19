# OpenCompany Gateway: BerriAI's official LiteLLM module on ECS Fargate, plus
# the few things the module leaves to the caller (TLS certificate, DNS record,
# the salt key, and the vendor-key secrets).

locals {
  # Keep in step with the module version below, the image tag in
  # gateway/local/docker-compose.yml and LITELLM_VERSION in the CI workflow
  # (gateway/tests/test_pins.py checks). 1.103.x is the only stable line with
  # the streamed-alias billing fix (LiteLLM PR #44341).
  litellm_version = "1.103.4"
  name            = "opencompany-gateway-${var.env}"

  common_env = {
    LITELLM_MODE   = "PRODUCTION"
    LITELLM_LOG    = "ERROR"
    PROXY_BASE_URL = "https://${var.gateway_hostname}"
    NO_DOCS        = "True"
    NO_REDOC       = "True"
    NO_OPENAPI     = "True"
    # Prices come from this reviewed copy of LiteLLM's price list. Without it
    # the gateway downloads upstream main on every start (prices change
    # unannounced), and its bundled list predates the current models.
    LITELLM_MODEL_COST_MAP_URL = "https://raw.githubusercontent.com/BerriAI/litellm/2f29a55a5102fbf23e406d313ea4b91a7257e48f/model_prices_and_context_window.json"
  }

  # Creates the two SpendLogs indexes that LiteLLM stopped creating in
  # migrations (1.103.3+). Runs as the app user, which owns the tables.
  spendlogs_index_sql = <<-SQL
    GRANT litellm_app TO postgres;
    SET ROLE litellm_app;
    CREATE INDEX CONCURRENTLY IF NOT EXISTS "LiteLLM_SpendLogs_api_key_startTime_idx" ON "LiteLLM_SpendLogs" ("api_key", "startTime");
    CREATE INDEX CONCURRENTLY IF NOT EXISTS "LiteLLM_SpendLogs_litellm_call_id_idx" ON "LiteLLM_SpendLogs" ("litellm_call_id");
  SQL
}

# LITELLM_SALT_KEY encrypts provider credentials stored in the database. It can
# never change once a credential is stored, so it is generated once and kept.
resource "random_password" "salt_key" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "salt_key" {
  name                    = "${local.name}/litellm-salt-key"
  description             = "LITELLM_SALT_KEY for the gateway and backend. Never change it."
  recovery_window_in_days = 30
}

resource "aws_secretsmanager_secret_version" "salt_key" {
  secret_id     = aws_secretsmanager_secret.salt_key.id
  secret_string = random_password.salt_key.result

  lifecycle {
    ignore_changes = [secret_string]
  }
}

# One empty secret per vendor key. Values are set out of band (see README),
# so no vendor key ever passes through Terraform state.
resource "aws_secretsmanager_secret" "vendor" {
  for_each = toset(var.secret_env_names)

  name                    = "${local.name}/env/${lower(replace(each.value, "_", "-"))}"
  description             = "${each.value} for the gateway service."
  recovery_window_in_days = 7
}

module "litellm" {
  source  = "BerriAI/litellm/aws"
  version = "1.103.4"

  region = var.region
  tenant = "opencompany"
  env    = var.env
  azs    = var.azs

  gateway_image    = "ghcr.io/berriai/litellm-gateway:v${local.litellm_version}"
  backend_image    = "ghcr.io/berriai/litellm-backend:v${local.litellm_version}"
  ui_image         = "ghcr.io/berriai/litellm-ui:v${local.litellm_version}"
  migrations_image = "ghcr.io/berriai/litellm-migrations:v${local.litellm_version}"

  acm_certificate_arn = aws_acm_certificate_validation.gateway.certificate_arn

  gateway_desired_count = var.gateway_min_tasks
  gateway_min_capacity  = var.gateway_min_tasks
  gateway_max_capacity  = var.gateway_max_tasks
  gateway_cpu_target    = 60
  # Never scale on memory: Prisma's query engine holds on to its peak.
  gateway_memory_target = 0
  # In-task PgBouncer caps each task's database connections.
  gateway_connection_pool_enabled = true

  proxy_config = yamldecode(file("${path.module}/../../litellm/config.yaml"))

  gateway_extra_secrets = merge(
    { for env_name, secret in aws_secretsmanager_secret.vendor : env_name => secret.arn },
    { LITELLM_SALT_KEY = aws_secretsmanager_secret.salt_key.arn },
  )
  backend_extra_secrets = {
    LITELLM_SALT_KEY = aws_secretsmanager_secret.salt_key.arn
  }
  gateway_extra_env = local.common_env
  backend_extra_env = local.common_env

  depends_on = [aws_secretsmanager_secret_version.salt_key]
}

# TLS certificate for the gateway hostname, validated through DNS.
resource "aws_acm_certificate" "gateway" {
  domain_name       = var.gateway_hostname
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "gateway_cert_validation" {
  for_each = {
    for option in aws_acm_certificate.gateway.domain_validation_options : option.domain_name => option
  }

  zone_id         = var.route53_zone_id
  name            = each.value.resource_record_name
  type            = each.value.resource_record_type
  records         = [each.value.resource_record_value]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "gateway" {
  certificate_arn         = aws_acm_certificate.gateway.arn
  validation_record_fqdns = [for record in aws_route53_record.gateway_cert_validation : record.fqdn]
}

resource "aws_route53_record" "gateway" {
  zone_id = var.route53_zone_id
  name    = var.gateway_hostname
  type    = "CNAME"
  ttl     = 60
  records = [module.litellm.alb_dns_name]
}

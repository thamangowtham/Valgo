/**
 * Authentication: Secrets Manager for credentials + Lambda for daily TOTP refresh.
 */

# Secrets — empty initially. Populate manually after `terraform apply`.
locals {
  secrets = [
    "valgo/kite/api_key",
    "valgo/kite/api_secret",
    "valgo/kite/user_id",
    "valgo/kite/password",
    "valgo/kite/totp_seed",
    "valgo/kite/access_token",       # populated daily by the Lambda
    "valgo/admin_api/token",
    "valgo/tradingview/shared_secret",
  ]
}

resource "aws_secretsmanager_secret" "valgo" {
  for_each = toset(local.secrets)
  name     = each.value
}

# IAM role for the daily auth Lambda
resource "aws_iam_role" "auth_refresh" {
  name = "valgo-${var.env}-auth-refresh"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "auth_refresh" {
  role = aws_iam_role.auth_refresh.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue", "secretsmanager:UpdateSecret", "secretsmanager:CreateSecret"]
        Resource = "arn:aws:secretsmanager:*:*:secret:valgo/*"
      },
      {
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = ["sns:Publish"]
        Resource = aws_sns_topic.alerts.arn
      },
    ]
  })
}

resource "aws_sns_topic" "alerts" {
  name = "valgo-${var.env}-alerts"
}

# The actual Lambda function — code packaged separately, deployed via:
#     cd services/auth_refresh && zip -r ../../infra/build/auth_refresh.zip .
resource "aws_lambda_function" "auth_refresh" {
  function_name = "valgo-${var.env}-auth-refresh"
  role          = aws_iam_role.auth_refresh.arn
  runtime       = "python3.11"
  handler       = "handler.handler"
  timeout       = 60
  memory_size   = 256

  filename         = "${path.module}/../../build/auth_refresh.zip"
  source_code_hash = fileexists("${path.module}/../../build/auth_refresh.zip") ? filebase64sha256("${path.module}/../../build/auth_refresh.zip") : null

  environment {
    variables = {
      AWS_REGION       = data.aws_region.current.name
      ALERT_SNS_TOPIC  = aws_sns_topic.alerts.arn
    }
  }

  lifecycle {
    ignore_changes = [filename, source_code_hash]
  }
}

data "aws_region" "current" {}

# Schedule: every weekday at 08:45 IST = 03:15 UTC
resource "aws_cloudwatch_event_rule" "daily_refresh" {
  name                = "valgo-${var.env}-daily-auth"
  schedule_expression = "cron(15 3 ? * MON-FRI *)"
}

resource "aws_cloudwatch_event_target" "daily_refresh" {
  rule = aws_cloudwatch_event_rule.daily_refresh.name
  arn  = aws_lambda_function.auth_refresh.arn
}

resource "aws_lambda_permission" "eventbridge_invoke" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auth_refresh.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_refresh.arn
}

output "secrets_manager_arns" {
  value = { for k, s in aws_secretsmanager_secret.valgo : k => s.arn }
}

output "alert_sns_topic_arn" { value = aws_sns_topic.alerts.arn }

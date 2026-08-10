locals {
  lambda_source_dir = "${path.module}/../src"

  # Keep bytecode a local test run left behind out of the artifact, so the zip
  # (and therefore source_code_hash) depends only on the source.
  lambda_excludes = toset([
    for file in fileset(local.lambda_source_dir, "**") :
    file if length(regexall("(^|/)__pycache__/", file)) > 0 || endswith(file, ".pyc")
  ])
}

# The function has no third-party dependencies, so the source tree zips
# directly — there is no build step between `git clone` and `terraform plan`.
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = local.lambda_source_dir
  output_path = "${path.module}/build/lambda.zip"
  excludes    = local.lambda_excludes
}

resource "aws_lambda_function" "todo" {
  function_name    = "${var.project_name}-action-group"
  role             = aws_iam_role.lambda.arn
  handler          = "todo_agent.lambda_handler.lambda_handler"
  runtime          = var.lambda_runtime
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  timeout          = var.lambda_timeout_seconds
  memory_size      = var.lambda_memory_mb

  environment {
    variables = {
      TODO_TABLE_NAME = aws_dynamodb_table.todo.name
      LOG_LEVEL       = "INFO"
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}

# Declared explicitly so retention is managed rather than "never expire".
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.project_name}-action-group"
  retention_in_days = var.log_retention_days
}

# No aws_lambda_permission here on purpose: the gateway invokes this function
# by assuming aws_iam_role.gateway, which is a same-account identity-based
# grant. A resource policy would be a second, redundant place to audit.

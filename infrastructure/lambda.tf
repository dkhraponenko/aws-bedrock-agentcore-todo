resource "aws_lambda_function" "todo" {
  function_name    = "${var.project_name}-tools"
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
  name              = "/aws/lambda/${var.project_name}-tools"
  retention_in_days = var.log_retention_days
}

# No aws_lambda_permission here on purpose: the gateway invokes this function
# by assuming aws_iam_role.gateway, which is a same-account identity-based
# grant. A resource policy would be a second, redundant place to audit.

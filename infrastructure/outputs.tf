output "harness_id" {
  description = "AgentCore harness id — the agent to invoke."
  value       = aws_bedrockagentcore_harness.todo.harness_id
}

output "harness_arn" {
  description = "AgentCore harness ARN."
  value       = aws_bedrockagentcore_harness.todo.arn
}

output "gateway_url" {
  description = "MCP endpoint the harness reaches the tools through."
  value       = aws_bedrockagentcore_gateway.todo.gateway_url
}

output "lambda_function_name" {
  description = "Tool Lambda, for tailing logs."
  value       = aws_lambda_function.todo.function_name
}

output "dynamodb_table_name" {
  description = "Table holding the todo items."
  value       = aws_dynamodb_table.todo.name
}

output "chat_command" {
  description = "Ready-to-run command for the manual smoke-test script."
  # Absolute script path, so `eval "$(terraform output -raw chat_command)"`
  # works from this directory rather than only from the repository root.
  value = join(" ", [
    "AWS_REGION=${var.aws_region}",
    "HARNESS_ARN=${aws_bedrockagentcore_harness.todo.arn}",
    "python ${abspath("${path.root}/../scripts/chat.py")}",
  ])
}

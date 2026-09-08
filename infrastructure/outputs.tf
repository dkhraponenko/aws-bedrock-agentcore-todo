output "agent_runtime_id" {
  description = "AgentCore Runtime id — the agent to invoke."
  value       = aws_bedrockagentcore_agent_runtime.todo.agent_runtime_id
}

output "agent_runtime_arn" {
  description = "AgentCore Runtime ARN."
  value       = aws_bedrockagentcore_agent_runtime.todo.agent_runtime_arn
}

output "gateway_url" {
  description = "MCP endpoint the agent reaches the tools through."
  value       = aws_bedrockagentcore_gateway.todo.gateway_url
}

output "memory_id" {
  description = "Conversation memory backing the agent, partitioned by user."
  value       = aws_bedrockagentcore_memory.conversations.id
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
  # Relative, because this is resolved where the apply runs and the apply runs
  # in Actions: abspath() baked the runner's own checkout path into the state
  # and handed it back to a laptop that has no such directory. path.root is
  # whatever the caller pointed terraform at, so the result stays correct from
  # infrastructure/ — which is where the README says to run this from.
  value = join(" ", [
    "AWS_REGION=${var.aws_region}",
    "AGENT_RUNTIME_ARN=${aws_bedrockagentcore_agent_runtime.todo.agent_runtime_arn}",
    "python ${path.root}/../scripts/chat.py",
  ])
}

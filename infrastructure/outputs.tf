# Facts about what was built, and nothing else. An output that composed a
# command to run would have to know where the script lives and what starts it,
# neither of which terraform is in a position to know: abspath() once baked the
# CI runner's own checkout path into the state and handed it to a laptop.
# scripts/sync_env.sh turns these into the .env the local client reads.

output "aws_region" {
  description = "Region the whole stack lives in."
  value       = var.aws_region
}

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

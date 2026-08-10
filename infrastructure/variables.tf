variable "project_name" {
  description = "Prefix applied to every resource name."
  type        = string
  default     = "todo-agent"
}

variable "aws_region" {
  description = "Region hosting the agent, the Lambda and the table."
  type        = string
  default     = "us-east-1"
}

variable "agent_model" {
  description = <<-EOT
    Foundation model the agent reasons with, as a cross-region inference
    profile. Change the "us." prefix together with aws_region (eu.* for EU
    regions) and keep var.agent_base_model in sync.
  EOT
  type        = string
  default     = "us.amazon.nova-pro-v1:0"
}

variable "agent_base_model" {
  description = <<-EOT
    The plain foundation-model id behind var.agent_model. Invoking through a
    profile requires bedrock:InvokeModel on the profile *and* on the underlying
    model in every region the profile can route to, so this is granted
    separately.
  EOT
  type        = string
  default     = "amazon.nova-pro-v1:0"
}

variable "lambda_runtime" {
  description = "Python runtime for the tool Lambda."
  type        = string
  default     = "python3.13"
}

variable "lambda_timeout_seconds" {
  description = "Lambda timeout. Generous: a tool call is a single DynamoDB request."
  type        = number
  default     = 30
}

variable "lambda_memory_mb" {
  description = "Lambda memory size in MB."
  type        = number
  default     = 256
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the Lambda log group."
  type        = number
  default     = 14
}

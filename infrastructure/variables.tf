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

variable "runtime_python_version" {
  description = "Python version AgentCore Runtime executes the agent loop with."
  type        = string
  default     = "PYTHON_3_13"
}

variable "runtime_entry_point" {
  description = <<-EOT
    The command AgentCore Runtime runs to start the agent. This is argv, not an
    import path: the last element is a .py file resolved against the root of the
    unpacked zip, and the service rejects anything else. Prefixing it with
    "opentelemetry-instrument" is how the documented traces get turned on.
  EOT
  type        = list(string)
  default     = ["main.py"]
}

variable "runtime_idle_timeout_seconds" {
  description = <<-EOT
    How long an idle conversation keeps its runtime session. Sessions bill for
    their lifetime, not for time spent working, so this bounds what an abandoned
    browser tab costs. The service default is 900.
  EOT
  type        = number
  default     = 300
}

variable "runtime_max_lifetime_seconds" {
  description = "Hard ceiling on one runtime session, after which it is replaced."
  type        = number
  default     = 3600
}

variable "memory_expiry_days" {
  description = "How long conversation events are kept. Chat history for a todo list, not a record."
  type        = number
  default     = 7
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
  description = "CloudWatch Logs retention, applied to both the Lambda's group and the runtime's."
  type        = number
  default     = 14
}

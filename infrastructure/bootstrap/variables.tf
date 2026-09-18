variable "state_bucket_name" {
  description = <<-EOT
    Bucket holding the main stack's Terraform state. Bucket names are globally
    unique, so this carries a suffix picked once at random rather than the
    account id, which would otherwise be readable in a public repository.

    It has to match the literal in ../backend.tf exactly: a backend block cannot
    read a variable, so the name lives in two places and nothing checks that
    they agree.
  EOT
  type        = string
  default     = "todo-agent-tfstate-791762"
}

variable "github_repository" {
  description = "The only repository allowed to assume the deploy role, as owner/name."
  type        = string
  default     = "dkhraponenko/aws-bedrock-agentcore-todo"
}

variable "github_owner_id" {
  description = <<-EOT
    Numeric id of the account, from https://api.github.com/users/<owner>. GitHub
    now issues immutable subjects carrying this and the repository id, so that
    renaming either one does not carry the trust across with it.
  EOT
  type        = string
  default     = "15193761"
}

variable "github_repository_id" {
  description = <<-EOT
    Numeric id of the repository, read from the subject claim of a real run: a
    failed AssumeRoleWithWebIdentity in CloudTrail carries it in `userName`.
    It changes when the repository is deleted and recreated under the same name,
    and STS then refuses without saying which claim failed.
  EOT
  type        = string
  default     = "1376290567"
}

variable "github_branch" {
  description = <<-EOT
    Branch whose workflow runs may assume the deploy role. Pinning the branch as
    well as the repository means a workflow dispatched from anywhere else is
    refused by STS, before it can read a secret or reach the state.
  EOT
  type        = string
  default     = "main"
}

variable "state_version_retention_days" {
  description = <<-EOT
    How long superseded versions of the state file are kept. This is the whole
    rollback window for a bad apply; the objects are a few kilobytes, so the
    number is about how far back you might want to reach, not about cost.
  EOT
  type        = number
  default     = 90
}

variable "project_name" {
  description = "Prefix applied to every resource name. Matches the main stack."
  type        = string
  default     = "todo-agent"
}

variable "aws_region" {
  description = "Region holding the state bucket. Matches the main stack."
  type        = string
  default     = "us-east-1"
}

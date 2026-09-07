output "state_bucket" {
  description = "Copy into the backend block in ../backend.tf; it cannot read this value itself."
  value       = aws_s3_bucket.state.id
}

output "deploy_role_arn" {
  description = "Store as the AWS_DEPLOY_ROLE_ARN secret under Settings - Secrets - Actions."
  value       = aws_iam_role.github_deploy.arn
}

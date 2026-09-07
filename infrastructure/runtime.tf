##############################################################################
# AgentCore Runtime: the agent loop itself
#
# This replaced the managed harness. The harness owned the loop and forwarded
# nothing about the end user to its tools, so every caller shared one todo list.
# Owning the loop is what lets the verified caller identity reach the table.
##############################################################################

# The runtime loads its code from S3 rather than from an inline blob, so the
# artifact needs somewhere to live.
resource "aws_s3_bucket" "artifacts" {
  bucket_prefix = "${var.project_name}-artifacts-"

  # Nothing here is a source of truth: every object is rebuilt from src/ by
  # `terraform apply`, so a destroy should not need a manual empty first.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# SSE-S3 rather than SSE-KMS, deliberately. A customer-managed key is $1/month
# standing charge plus a request charge, against a stack whose whole idle cost is
# about $0.001/month, and it buys key rotation and a key policy that nothing here
# has a use for.
#trivy:ignore:AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# The content hash is in the key, so a code change writes a new object and the
# runtime below sees a changed prefix — which is what forces it to redeploy.
resource "aws_s3_object" "runtime" {
  bucket = aws_s3_bucket.artifacts.id
  key    = "runtime/${data.archive_file.runtime.output_base64sha256}.zip"
  source = data.archive_file.runtime.output_path
  etag   = data.archive_file.runtime.output_md5
}

resource "aws_bedrockagentcore_agent_runtime" "todo" {
  # Same naming rule as the harness had: letters, digits and underscores only.
  agent_runtime_name = "${replace(var.project_name, "-", "_")}_runtime"
  role_arn           = aws_iam_role.runtime.arn
  description        = "Converse tool-use loop over the todo gateway."

  agent_runtime_artifact {
    code_configuration {
      runtime = var.runtime_python_version

      # Confirmed against the service on first apply; see var.runtime_entry_point.
      entry_point = var.runtime_entry_point

      code {
        s3 {
          bucket = aws_s3_bucket.artifacts.id
          prefix = aws_s3_object.runtime.key
        }
      }
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  # Memory is billed for the lifetime of a session, not for time spent working,
  # so an abandoned browser tab keeps costing until this expires. The service
  # default is 900s; this is deliberately shorter.
  lifecycle_configuration = [{
    idle_runtime_session_timeout = var.runtime_idle_timeout_seconds
    max_lifetime                 = var.runtime_max_lifetime_seconds
  }]

  environment_variables = {
    GATEWAY_URL = aws_bedrockagentcore_gateway.todo.gateway_url
    MEMORY_ID   = aws_bedrockagentcore_memory.conversations.id
    AGENT_MODEL = var.agent_model
    # Passed as configuration rather than shipped in the zip, so the prompt has
    # one home and editing it does not rebuild the artifact.
    AGENT_INSTRUCTION = local.agent_instruction
    LOG_LEVEL         = "INFO"
  }

  # The loop calls tools/list on its first turn, so the target has to exist.
  depends_on = [aws_bedrockagentcore_gateway_target.todo]
}

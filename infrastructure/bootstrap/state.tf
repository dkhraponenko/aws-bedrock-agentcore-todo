##############################################################################
# Terraform state
#
# Once apply runs on a GitHub runner, state cannot stay on a laptop: the runner
# is empty every time, and a run that starts from no state tries to create the
# whole stack again — a name conflict on the table, a second artifact bucket,
# and a live runtime nothing tracks any more.
#
# Versioning is the point of the bucket, not a checkbox. State is the one file
# in this project with no other copy, and the only way back from a bad apply is
# the previous version of this object.
##############################################################################

resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name

  # No force_destroy here, unlike the artifact bucket in ../runtime.tf. Every
  # object there is rebuilt from src/ by the next apply; every object here is
  # the only record that the infrastructure exists.
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3 rather than SSE-KMS, deliberately. A customer-managed key is $1/month
# standing charge plus a request charge, against a stack whose whole idle cost is
# about $0.001/month, and it buys key rotation and a key policy that nothing here
# has a use for.
#trivy:ignore:AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Without this every apply leaves a version behind for ever. The current
# version is never touched, so the rollback window stays open and the bucket
# stops growing.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-superseded-state"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.state_version_retention_days
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

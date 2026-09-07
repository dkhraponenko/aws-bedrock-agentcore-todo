##############################################################################
# The role GitHub Actions assumes
#
# The alternative was an access key pair in GitHub's storage: a permanent
# credential to this account that nobody would ever rotate. This role is
# reachable only through a token GitHub signs for one repository on one branch,
# and the credentials it hands back last an hour.
##############################################################################

locals {
  github_owner = split("/", var.github_repository)[0]
  github_name  = split("/", var.github_repository)[1]

  # Two spellings of the same repository on the same branch, because the subject
  # format is a repository setting. The immutable one is what this repository
  # sends today; the classic one is what it would send if that setting were
  # turned off. Listing both beats discovering the difference through STS, which
  # refuses without ever saying which claim failed.
  #
  # Neither is a wildcard on purpose. `dkhraponenko*` would also match an account
  # someone else could register.
  allowed_subjects = [
    "repo:${local.github_owner}@${var.github_owner_id}/${local.github_name}@${var.github_repository_id}:ref:refs/heads/${var.github_branch}",
    "repo:${var.github_repository}:ref:refs/heads/${var.github_branch}",
  ]
}

data "aws_iam_policy_document" "github_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    # Both conditions are load-bearing. Without the audience check any token the
    # provider issued would pass; without the subject check that means any
    # repository on GitHub. The subject is pinned down to the branch, so a
    # workflow dispatched from elsewhere fails in STS rather than in the job.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.allowed_subjects
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name                 = "${var.project_name}-github-deploy-role"
  description          = "Assumed by GitHub Actions to plan, apply and destroy the todo stack."
  assume_role_policy   = data.aws_iam_policy_document.github_assume_role.json
  max_session_duration = 3600
}

# The wildcards below are the point of this role, not an oversight: Terraform has
# to create the resources, and the alternative is enumerating every action the AWS
# provider issues and rediscovering the gaps one failed apply at a time. The
# ProtectOwnRole, ProtectIdentityProvider and ProtectStateBucket denies are what
# bound it.
#trivy:ignore:AWS-0345
data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid       = "ReadWriteState"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/*"]
  }

  # The S3-native lock writes a sibling object and needs to see the bucket.
  statement {
    sid       = "ListState"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }

  # Every service the main stack actually creates, and nothing beyond it.
  # bedrock is absent on purpose: ../iam.tf builds the model ARNs as strings, so
  # an apply never calls Bedrock. InvokeModel belongs to the runtime role.
  statement {
    sid    = "StackServices"
    effect = "Allow"
    actions = [
      "iam:*",
      "s3:*",
      "dynamodb:*",
      "lambda:*",
      "logs:*",
      "bedrock-agentcore:*",
    ]
    resources = ["*"]
  }

  # iam:* above is administrator in all but name — anything that can write an
  # IAM policy can widen its own. These denies are what keeps that from being
  # irreversible: the role cannot rewrite itself, cannot remove the provider
  # that lets it in, and cannot delete the bucket its own state lives in. A
  # destroy pointed at the wrong root fails here instead of locking CI out
  # permanently.
  statement {
    sid    = "ProtectOwnRole"
    effect = "Deny"
    actions = [
      "iam:DeleteRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:PutRolePermissionsBoundary",
    ]
    resources = [aws_iam_role.github_deploy.arn]
  }

  statement {
    sid    = "ProtectIdentityProvider"
    effect = "Deny"
    actions = [
      "iam:DeleteOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
      "iam:RemoveClientIDFromOpenIDConnectProvider",
    ]
    resources = [aws_iam_openid_connect_provider.github.arn]
  }

  statement {
    sid    = "ProtectStateBucket"
    effect = "Deny"
    actions = [
      "s3:DeleteBucket",
      "s3:PutBucketPolicy",
      "s3:PutBucketVersioning",
    ]
    resources = [aws_s3_bucket.state.arn]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "${var.project_name}-github-deploy-policy"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy.json
}

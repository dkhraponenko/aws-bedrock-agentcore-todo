##############################################################################
# Lambda execution role
##############################################################################

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.project_name}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid    = "WriteLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }

  # Exactly the five calls the store makes, on exactly one table.
  statement {
    sid    = "TodoTableAccess"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
    ]
    resources = [aws_dynamodb_table.todo.arn]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${var.project_name}-lambda-policy"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

##############################################################################
# Shared trust policy for the two AgentCore roles
##############################################################################

data "aws_iam_policy_document" "agentcore_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }

    # Confused-deputy guard. Only aws:SourceAccount: CreateGatewayTarget
    # validates the role by assuming it, and that call does not carry
    # aws:SourceArn — an ArnLike condition on it evaluates false and the API
    # returns "Gateway service is not authorized to perform AssumeRole".
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

##############################################################################
# Gateway role — what the gateway may do on behalf of any authorized caller
##############################################################################

resource "aws_iam_role" "gateway" {
  name               = "${var.project_name}-gateway-role"
  assume_role_policy = data.aws_iam_policy_document.agentcore_assume_role.json
}

# This role is the upper bound on everything reachable through the gateway,
# so it grants one action on one function and nothing else.
data "aws_iam_policy_document" "gateway" {
  statement {
    sid       = "InvokeTodoLambda"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.todo.arn]
  }
}

resource "aws_iam_role_policy" "gateway" {
  name   = "${var.project_name}-gateway-policy"
  role   = aws_iam_role.gateway.id
  policy = data.aws_iam_policy_document.gateway.json
}

##############################################################################
# Runtime role — what the agent loop itself may do
##############################################################################

resource "aws_iam_role" "runtime" {
  name               = "${var.project_name}-runtime-role"
  assume_role_policy = data.aws_iam_policy_document.agentcore_assume_role.json
}

data "aws_iam_policy_document" "runtime" {
  statement {
    sid    = "InvokeFoundationModel"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      # The inference profile the loop is configured with...
      "arn:aws:bedrock:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:inference-profile/${var.agent_model}",
      # ...and the underlying model in every region that profile may route to.
      "arn:aws:bedrock:*::foundation-model/${var.agent_base_model}",
    ]
  }

  # The loop reaches its tools by signing MCP requests to the gateway as itself.
  # There is no boto3 operation for this; the action authorises the raw
  # endpoint, which src/todo_runtime/mcp.py calls directly.
  statement {
    sid       = "InvokeTodoGateway"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeGateway"]
    resources = [aws_bedrockagentcore_gateway.todo.gateway_arn]
  }

  # Exactly the two calls src/todo_runtime/memory.py makes, on the one memory
  # resource — the harness needed a name-prefix wildcard here because it created
  # its own memory outside Terraform.
  statement {
    sid    = "ConversationMemory"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:CreateEvent",
      "bedrock-agentcore:ListEvents",
    ]
    resources = [aws_bedrockagentcore_memory.conversations.arn]
  }

  # The service fetches the code archive as this role.
  statement {
    sid       = "ReadCodeArtifact"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
  }

  statement {
    sid    = "WriteLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/*",
    ]
  }
}

resource "aws_iam_role_policy" "runtime" {
  name   = "${var.project_name}-runtime-policy"
  role   = aws_iam_role.runtime.id
  policy = data.aws_iam_policy_document.runtime.json
}

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
# Harness role — what the agent loop itself may do
##############################################################################

resource "aws_iam_role" "harness" {
  name               = "${var.project_name}-harness-role"
  assume_role_policy = data.aws_iam_policy_document.agentcore_assume_role.json
}

data "aws_iam_policy_document" "harness" {
  statement {
    sid    = "InvokeFoundationModel"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [
      # The inference profile the harness is configured with...
      "arn:aws:bedrock:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:inference-profile/${var.agent_model}",
      # ...and the underlying model in every region that profile may route to.
      "arn:aws:bedrock:*::foundation-model/${var.agent_base_model}",
    ]
  }

  # Outbound auth is aws_iam, so the harness reaches its tools by calling the
  # gateway as itself.
  statement {
    sid       = "InvokeTodoGateway"
    effect    = "Allow"
    actions   = ["bedrock-agentcore:InvokeGateway"]
    resources = [aws_bedrockagentcore_gateway.todo.gateway_arn]
  }

  # The harness keeps conversation history in a memory resource it creates for
  # itself on first invocation, named harness_<harness_name>_<hash>. It is not
  # a Terraform resource, so the grant is scoped by name prefix instead of ARN.
  statement {
    sid    = "HarnessConversationMemory"
    effect = "Allow"
    actions = [
      "bedrock-agentcore:GetMemory",
      "bedrock-agentcore:CreateEvent",
      "bedrock-agentcore:GetEvent",
      "bedrock-agentcore:ListEvents",
      "bedrock-agentcore:ListActors",
      "bedrock-agentcore:ListSessions",
      "bedrock-agentcore:GetMemoryRecord",
      "bedrock-agentcore:ListMemoryRecords",
      "bedrock-agentcore:RetrieveMemoryRecords",
    ]
    resources = [
      "arn:aws:bedrock-agentcore:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:memory/harness_*",
    ]
  }
}

resource "aws_iam_role_policy" "harness" {
  name   = "${var.project_name}-harness-policy"
  role   = aws_iam_role.harness.id
  policy = data.aws_iam_policy_document.harness.json
}

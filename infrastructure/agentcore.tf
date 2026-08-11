##############################################################################
# Bedrock AgentCore: gateway (tools) + harness (the agent itself)
##############################################################################

# The tool contract and the system prompt live in data files rather than in
# HCL so the tests and scripts/ can read the same bytes the gateway publishes.
locals {
  tools             = jsondecode(file("${path.module}/tools.json"))
  agent_instruction = file("${path.module}/agent_instruction.md")
}

# The gateway is the MCP facade in front of the Lambda. AWS_IAM inbound auth
# keeps this a pure IAM problem — CUSTOM_JWT would drag in Cognito or another
# OIDC provider for what is a single-operator stand.
resource "aws_bedrockagentcore_gateway" "todo" {
  name            = "${var.project_name}-gateway"
  role_arn        = aws_iam_role.gateway.arn
  protocol_type   = "MCP"
  authorizer_type = "AWS_IAM"
  description     = "MCP facade exposing the todo Lambda as five tools."

  protocol_configuration {
    mcp {
      instructions = "Tools for reading and writing one user's todo list."
    }
  }
}

# The target publishes the tool contract the model sees, expanded from
# tools.json. The parameter descriptions in that file are what the model
# reasons over — they earn their length.
resource "aws_bedrockagentcore_gateway_target" "todo" {
  gateway_identifier = aws_bedrockagentcore_gateway.todo.gateway_id
  name               = "todo"
  description        = "CRUD operations against the user's todo list."

  # The gateway calls the Lambda as itself, using its own execution role.
  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.todo.arn

        tool_schema {
          dynamic "inline_payload" {
            for_each = local.tools

            content {
              name        = inline_payload.value.name
              description = inline_payload.value.description

              input_schema {
                type = "object"

                dynamic "property" {
                  for_each = inline_payload.value.properties

                  content {
                    name        = property.value.name
                    type        = property.value.type
                    description = property.value.description
                    required    = property.value.required
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}

# The harness is the managed orchestration loop: model, instruction, tools —
# the agent itself.
resource "aws_bedrockagentcore_harness" "todo" {
  # harnessName must match [a-zA-Z][a-zA-Z0-9_]{0,39} — no hyphens, unlike
  # every other name in this stack.
  harness_name       = "${replace(var.project_name, "-", "_")}_harness"
  execution_role_arn = aws_iam_role.harness.arn
  max_iterations     = 10

  model {
    bedrock_model_config {
      model_id = var.agent_model
    }
  }

  system_prompt {
    text = local.agent_instruction
  }

  tool {
    name = "todo"
    type = "agentcore_gateway"

    config {
      agentcore_gateway {
        gateway_arn = aws_bedrockagentcore_gateway.todo.gateway_arn

        # The harness calls the gateway with its own execution role rather
        # than an OAuth token, which is why no credential provider is needed.
        outbound_auth {
          aws_iam = true
        }
      }
    }
  }

  depends_on = [aws_bedrockagentcore_gateway_target.todo]
}

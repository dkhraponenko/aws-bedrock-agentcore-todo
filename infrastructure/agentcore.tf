##############################################################################
# Bedrock AgentCore: gateway (tools) + harness (the agent itself)
##############################################################################

locals {
  agent_instruction = <<-EOT
    You manage a personal todo list on behalf of the user. You have five tools:
    add_item, list_items, search_items, update_item and delete_item.

    update_item and delete_item accept an item_id and nothing else that
    identifies the item. When the user refers to a task by its wording rather
    than its id ("delete buy a milk", "mark the bank call as done"), call
    search_items first to resolve the wording to an item_id, then act on that
    id. Never invent or guess an item_id.

    If search_items returns exactly one match, act on it. If it returns
    several, list the matches and ask the user which one they mean. If it
    returns none, say so instead of creating something new.

    Confirm what you did in one short sentence, referring to tasks by their
    text rather than their id. Ids are for tool calls, not for the user.
  EOT
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

# The target carries the tool contract the model sees. The parameter
# descriptions are what the model reasons over — they earn their length.
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
          inline_payload {
            name        = "add_item"
            description = "Add a new task to the user's todo list."

            input_schema {
              type = "object"

              property {
                name        = "text"
                type        = "string"
                description = "What the task is, in the user's own words. For example: buy a milk."
                required    = true
              }

              property {
                name        = "priority"
                type        = "integer"
                description = "Priority from 1 (highest) to 5 (lowest). Defaults to 3 when the user does not say."
                required    = false
              }
            }
          }

          inline_payload {
            name        = "list_items"
            description = "Return every task on the user's list, oldest first. Use this when the user asks to see the whole list."

            input_schema {
              type = "object"

              property {
                name        = "status"
                type        = "string"
                description = "Optional filter, either PENDING or DONE. Omit to return tasks in any state."
                required    = false
              }
            }
          }

          inline_payload {
            name        = "search_items"
            description = "Find tasks whose text matches a query, case-insensitively. Returns the item_id of each match. Call this before update_item or delete_item whenever the user identifies a task by its wording instead of an id."

            input_schema {
              type = "object"

              property {
                name        = "query"
                type        = "string"
                description = "Words to look for in the task text, for example: milk."
                required    = true
              }

              property {
                name        = "status"
                type        = "string"
                description = "Optional filter, either PENDING or DONE."
                required    = false
              }
            }
          }

          inline_payload {
            name        = "update_item"
            description = "Change the text and/or the status of one existing task. Requires an item_id obtained from search_items or list_items. At least one of text or status must be supplied."

            input_schema {
              type = "object"

              property {
                name        = "item_id"
                type        = "string"
                description = "Id of the task to update, as returned by search_items or list_items."
                required    = true
              }

              property {
                name        = "text"
                type        = "string"
                description = "New task text. Omit to leave the wording unchanged."
                required    = false
              }

              property {
                name        = "status"
                type        = "string"
                description = "New status, either PENDING or DONE. Omit to leave the status unchanged."
                required    = false
              }
            }
          }

          inline_payload {
            name        = "delete_item"
            description = "Permanently remove one task. Requires an item_id obtained from search_items or list_items."

            input_schema {
              type = "object"

              property {
                name        = "item_id"
                type        = "string"
                description = "Id of the task to delete, as returned by search_items or list_items."
                required    = true
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

##############################################################################
# Conversation memory
#
# The harness created this for itself and deleted it with itself. Declaring it
# means it can be granted by ARN instead of by a name prefix, and that its
# retention is a decision rather than a service default.
##############################################################################

resource "aws_bedrockagentcore_memory" "conversations" {
  name        = "${replace(var.project_name, "-", "_")}_conversations"
  description = "Short-term conversation history, one actor per authenticated user."

  # Short-term memory bills per event stored. Nothing here is worth keeping for
  # long: it is chat history for a todo list, not a record.
  event_expiry_duration = var.memory_expiry_days
}

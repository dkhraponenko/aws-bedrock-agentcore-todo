"""Lambda entry point for the todo tools behind the AgentCore gateway."""

from __future__ import annotations

import logging
import os
from typing import Any

from todo_agent.service import TodoService


logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# Cold start: build the DynamoDB client once and reuse it across invocations.
TodoService.setup()


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ANN401
    """Handle one AgentCore Gateway tool invocation.

    `event` carries the tool's arguments; `context.client_context.custom`
    carries which tool was called, so both are needed.
    """
    return TodoService.process(event, context)

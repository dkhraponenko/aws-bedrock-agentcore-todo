"""One turn: build the agent once at import, stream the turn as it happens.

`todo_runtime.server` is what AgentCore starts; this module is what it calls per
request and knows nothing about the transport.

Identity arrives in the payload and cannot be re-checked here, because the
runtime sees an IAM principal rather than an end user. The trust boundary is
therefore whoever holds `InvokeAgentRuntime`: the isolation downstream is real,
what is missing is a client that establishes who the user is.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config

from todo_logging.json_logs import configure
from todo_runtime.agent import MAX_ITERATIONS, AgentConfig, TodoAgent
from todo_runtime.mcp import GatewayClient
from todo_runtime.memory import ConversationMemory


if TYPE_CHECKING:
    from collections.abc import Iterator


# Installs the JSON formatter as well as the level: without it the `extra=`
# context below is attached to each record and never printed.
configure(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# Defaulting a session is harmless. The caller has no equivalent default: a
# stand-in would put unrelated people in one partition.
DEFAULT_SESSION_ID = "default"


def _instruction() -> str:
    r"""Return the system prompt with its paragraphs put back.

    AgentCore refuses an environment variable carrying a control character, so
    `runtime.tf` sends the newlines as the two characters `\n`.
    """
    return _require_env("AGENT_INSTRUCTION").replace("\\n", "\n")


def _require_env(name: str) -> str:
    """Return a required setting, or fail naming the variable that is missing."""
    value = os.environ.get(name, "").strip()
    if not value:
        msg = f"Missing required environment variable {name}."
        raise ValueError(msg)
    return value


class AgentService:
    """Holds the agent across invocations so each turn reuses one MCP session."""

    _agent: TodoAgent | None = None

    @classmethod
    def setup(cls) -> None:
        """Build the model client, the gateway client and the memory binding.

        Runs at import: a missing setting should stop the first invocation with
        the variable's name, not surface deep inside someone's conversation.
        """
        region = os.environ.get("AWS_REGION", "us-east-1")
        session = boto3.Session()

        cls._agent = TodoAgent(
            bedrock=session.client(
                "bedrock-runtime",
                config=Config(connect_timeout=5, read_timeout=120, retries={"max_attempts": 2}),
            ),
            gateway=GatewayClient(
                url=_require_env("GATEWAY_URL"),
                region=region,
                credentials=session.get_credentials(),
            ),
            memory=ConversationMemory(
                client=session.client("bedrock-agentcore"),
                memory_id=_require_env("MEMORY_ID"),
            ),
            config=AgentConfig(
                model_id=_require_env("AGENT_MODEL"),
                system_prompt=_instruction(),
                max_iterations=int(os.environ.get("MAX_ITERATIONS", str(MAX_ITERATIONS))),
            ),
        )

    @classmethod
    def stream(cls, payload: dict[str, Any]) -> Iterator[str]:
        """Run one turn, yielding `data: {...}` frames and then a `done` event.

        The frames are built here, not in `todo_runtime.server`, so the server
        stays a pipe. `payload` is `{"prompt", "user_id", "session_id"}`; a turn
        missing either of the first two is answered with an error frame rather
        than a substituted value.

        Raises:
            RuntimeError: `setup()` was never called.
        """
        if cls._agent is None:
            msg = "AgentService not initialized. Call setup() first."
            raise RuntimeError(msg)

        prompt = str(payload.get("prompt", "")).strip()
        user_id = str(payload.get("user_id") or "").strip()
        session_id = str(payload.get("session_id") or DEFAULT_SESSION_ID)

        if not prompt:
            yield _frame({"type": "error", "message": "Empty prompt."})
            yield _frame({"type": "done"})
            return

        if not user_id:
            logger.error("Refused a turn with no caller identity")
            yield _frame({"type": "error", "message": "Missing user_id."})
            yield _frame({"type": "done"})
            return

        try:
            for event in cls._agent.run(user_id, session_id, prompt):
                yield _frame(event)
        except Exception as e:
            logger.exception("Turn failed", extra={"user_id": user_id})
            yield _frame({"type": "error", "message": f"{type(e).__name__}: {e}"})
            yield _frame({"type": "done"})


def _frame(event: dict[str, Any]) -> str:
    """Render one event as a server-sent event frame."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


AgentService.setup()

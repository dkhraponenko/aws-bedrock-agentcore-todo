"""AgentCore Runtime entry point: build the agent once, stream one turn per call.

Identity arrives in the payload rather than being derived here, and this module
cannot re-check it: the runtime sees an IAM principal, not an end user. The
trust boundary is therefore whoever holds `InvokeAgentRuntime` on this runtime
— today only the operator running `scripts/chat.py`, which sends whatever
`USER_ID` says. Everything downstream partitions by that value, so the
isolation is real end to end; what is missing is a client that *establishes*
who the user is and puts a verified subject in the payload.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config

from todo_runtime.agent import MAX_ITERATIONS, AgentConfig, TodoAgent
from todo_runtime.mcp import GatewayClient
from todo_runtime.memory import ConversationMemory


if TYPE_CHECKING:
    from collections.abc import Iterator


logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

DEFAULT_USER_ID = "anonymous"
DEFAULT_SESSION_ID = "default"


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
                system_prompt=_require_env("AGENT_INSTRUCTION"),
                max_iterations=int(os.environ.get("MAX_ITERATIONS", str(MAX_ITERATIONS))),
            ),
        )

    @classmethod
    def stream(cls, payload: dict[str, Any]) -> Iterator[str]:
        """Run one turn, yielding server-sent events.

        SSE is produced here rather than in the proxy so that the proxy stays a
        pipe: it forwards bytes and adds nothing of its own.

        Args:
            payload: `{"prompt": str, "user_id": str, "session_id": str}`.

        Yields:
            `data: {...}` frames, ending with a `done` event.

        Raises:
            RuntimeError: `setup()` was never called.
        """
        if cls._agent is None:
            msg = "AgentService not initialized. Call setup() first."
            raise RuntimeError(msg)

        prompt = str(payload.get("prompt", "")).strip()
        user_id = str(payload.get("user_id") or DEFAULT_USER_ID)
        session_id = str(payload.get("session_id") or DEFAULT_SESSION_ID)

        if not prompt:
            yield _frame({"type": "error", "message": "Empty prompt."})
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


def invoke(payload: dict[str, Any]) -> Iterator[str]:
    """Entry point named by the runtime's `entry_point` configuration."""
    return AgentService.stream(payload)

"""Value objects for todo items and the gateway tool invocation."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


DEFAULT_USER_ID = "demo-user"
MIN_PRIORITY = 1
MAX_PRIORITY = 5
DEFAULT_PRIORITY = 3


class TodoStatus(StrEnum):
    """Lifecycle of a single todo item."""

    PENDING = "PENDING"
    DONE = "DONE"


def new_item_id() -> str:
    """Return a short, lexicographically time-sortable item id.

    Sortable ids make DynamoDB's sort-key ordering meaningful, so a Query
    returns items oldest-first without a secondary index.
    """
    return f"{int(time.time() * 1000):011x}{secrets.token_hex(3)}"


def utc_now() -> str:
    """Return the current time as an ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class TodoItem:
    """A single todo item as stored in DynamoDB."""

    user_id: str
    item_id: str
    text: str
    status: TodoStatus
    priority: int
    created_at: str
    updated_at: str

    def to_dynamodb(self) -> dict[str, Any]:
        """Serialise to the attribute map DynamoDB expects."""
        return {
            "user_id": self.user_id,
            "item_id": self.item_id,
            "text": self.text,
            "status": str(self.status),
            "priority": self.priority,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dynamodb(cls, raw: dict[str, Any]) -> TodoItem:
        """Build an item from a DynamoDB attribute map."""
        return cls(
            user_id=str(raw["user_id"]),
            item_id=str(raw["item_id"]),
            text=str(raw["text"]),
            status=TodoStatus(str(raw["status"])),
            priority=int(raw["priority"]),
            created_at=str(raw["created_at"]),
            updated_at=str(raw["updated_at"]),
        )

    def to_agent(self) -> dict[str, Any]:
        """Serialise the fields the agent needs to reason about the item.

        `user_id` is omitted: the agent never chooses it, and returning it would
        invite the model to pass it back.
        """
        return {
            "item_id": self.item_id,
            "text": self.text,
            "status": str(self.status),
            "priority": self.priority,
            "created_at": self.created_at,
        }


# AgentCore Gateway names a tool "<target>___<tool>" so that tools coming from
# different targets cannot collide.
TOOL_NAME_KEY = "bedrockAgentCoreToolName"
TOOL_NAME_SEPARATOR = "___"

# Carries the caller's identity alongside the tool's own arguments. The gateway
# does not publish it as a parameter, so it can only have come from the runtime.
USER_ID_KEY = "user_id"


@dataclass(frozen=True)
class ToolInvocation:
    """One tool call arriving from AgentCore Gateway.

    The arguments arrive as the whole event and the tool's name out of band, in
    the Lambda client context, so the two are recombined here.
    """

    tool_name: str
    arguments: dict[str, Any]
    user_id: str

    @classmethod
    def from_invocation(cls, event: dict[str, Any], context: Any) -> ToolInvocation:  # noqa: ANN401
        """Parse the gateway's event and untyped LambdaContext into one record."""
        client_context = getattr(context, "client_context", None)
        custom = getattr(client_context, "custom", None) or {}
        raw_name = str(custom.get(TOOL_NAME_KEY, ""))

        arguments = dict(event or {})

        return cls(
            # Drop the target prefix: handlers key off the bare tool name.
            tool_name=raw_name.rpartition(TOOL_NAME_SEPARATOR)[2],
            # Removed from the arguments: it identifies the caller rather than
            # describing the task, and leaving it in would let it reach a
            # handler as if the model had chosen it.
            arguments={key: value for key, value in arguments.items() if key != USER_ID_KEY},
            # Injected by the agent runtime from an identity AWS validated before
            # the turn began. It is not in the published tool schema, so a model
            # that invents one is overwritten upstream, never trusted here. The
            # fallback keeps direct invocations (tests, the offline runner)
            # working without an identity.
            user_id=str(arguments.get(USER_ID_KEY) or "").strip() or DEFAULT_USER_ID,
        )

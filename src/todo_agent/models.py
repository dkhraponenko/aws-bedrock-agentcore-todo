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
        """Parse the gateway's event and context into one invocation record.

        `context` is the AWS-supplied LambdaContext; it is untyped at this
        boundary, and only its `client_context.custom` map is read.
        """
        client_context = getattr(context, "client_context", None)
        custom = getattr(client_context, "custom", None) or {}
        raw_name = str(custom.get(TOOL_NAME_KEY, ""))

        return cls(
            # Drop the target prefix: handlers key off the bare tool name.
            tool_name=raw_name.rpartition(TOOL_NAME_SEPARATOR)[2],
            arguments=dict(event or {}),
            # AgentCore has no session-attribute channel, so every caller
            # shares one list. See the README for what a real deployment
            # would do instead.
            user_id=DEFAULT_USER_ID,
        )

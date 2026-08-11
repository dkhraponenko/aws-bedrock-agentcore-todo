"""Conversation history in AgentCore Memory, partitioned by actor.

Owning the loop means owning the history too, which is where per-user isolation
stops being a service default.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from operator import itemgetter
from typing import Any, Protocol


logger = logging.getLogger(__name__)

# One turn is a user message plus an assistant message, so this is the last
# dozen turns — enough context to resolve "delete that one" without resending
# an unbounded transcript on every call.
MAX_HISTORY_EVENTS = 24

_TO_MEMORY_ROLE = {"user": "USER", "assistant": "ASSISTANT"}
_FROM_MEMORY_ROLE = {"USER": "user", "ASSISTANT": "assistant"}


class MemoryClient(Protocol):
    """The two AgentCore Memory calls this module makes."""

    def create_event(self, **kwargs: Any) -> dict[str, Any]: ...  # noqa: ANN401

    def list_events(self, **kwargs: Any) -> dict[str, Any]: ...  # noqa: ANN401


class ConversationMemory:
    """Reads and appends the plain text turns of one conversation.

    Tool calls are left out: Converse requires every `toolUse` to be answered by
    a matching `toolResult` in the same sequence, so replaying half-finished
    exchanges from storage risks a malformed request for no benefit.
    """

    def __init__(self, client: MemoryClient, memory_id: str) -> None:
        self._client = client
        self._memory_id = memory_id

    def load(self, actor_id: str, session_id: str) -> list[dict[str, Any]]:
        """Return prior turns as Converse messages, oldest first.

        Partitioned by `actor_id`, so one user never sees another's history.
        Empty for a new conversation.
        """
        response = self._client.list_events(
            memoryId=self._memory_id,
            actorId=actor_id,
            sessionId=session_id,
            includePayloads=True,
            maxResults=MAX_HISTORY_EVENTS,
        )

        # ListEvents returns newest first; Converse needs chronological order.
        events = sorted(response.get("events", []), key=itemgetter("eventTimestamp"))
        return [message for event in events for message in _to_messages(event)]

    def append(self, actor_id: str, session_id: str, role: str, text: str) -> None:
        """Store one spoken turn. Blank text is skipped rather than stored."""
        if not text.strip():
            return

        self._client.create_event(
            memoryId=self._memory_id,
            actorId=actor_id,
            sessionId=session_id,
            eventTimestamp=datetime.now(UTC),
            payload=[{"conversational": {"role": _TO_MEMORY_ROLE[role], "content": {"text": text}}}],
        )


def _to_messages(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert one stored event into Converse messages, dropping unknown roles."""
    messages = []
    for entry in event.get("payload", []):
        conversational = entry.get("conversational")
        if conversational is None:
            continue
        role = _FROM_MEMORY_ROLE.get(conversational.get("role", ""))
        text = conversational.get("content", {}).get("text", "")
        if role and text:
            messages.append({"role": role, "content": [{"text": text}]})
    return messages

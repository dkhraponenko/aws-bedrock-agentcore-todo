"""Conversation memory: ordering, role mapping and what is deliberately not stored."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from todo_runtime.memory import MAX_HISTORY_EVENTS, ConversationMemory, MemoryClient


def event(role: str, text: str, minute: int) -> dict[str, Any]:
    """One stored conversational event at a fixed point in time."""
    return {
        "eventTimestamp": datetime(2026, 8, 11, 12, minute, tzinfo=UTC),
        "payload": [{"conversational": {"role": role, "content": {"text": text}}}],
    }


@pytest.fixture
def client() -> MagicMock:
    return MagicMock(spec=MemoryClient)


@pytest.fixture
def memory(client: MagicMock) -> ConversationMemory:
    return ConversationMemory(client, memory_id="memory-test")


def test_history_is_returned_oldest_first(client: MagicMock, memory: ConversationMemory) -> None:
    """ListEvents answers newest first; Converse needs the opposite."""
    client.list_events.return_value = {
        "events": [event("ASSISTANT", "second", 2), event("USER", "first", 1)],
    }

    assert memory.load("alice", "session-1") == [
        {"role": "user", "content": [{"text": "first"}]},
        {"role": "assistant", "content": [{"text": "second"}]},
    ]


def test_load_is_scoped_to_one_actor_and_session(client: MagicMock, memory: ConversationMemory) -> None:
    client.list_events.return_value = {"events": []}

    memory.load("alice", "session-1")

    client.list_events.assert_called_once_with(
        memoryId="memory-test",
        actorId="alice",
        sessionId="session-1",
        includePayloads=True,
        maxResults=MAX_HISTORY_EVENTS,
    )


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        pytest.param([{"blob": {"data": "x"}}], "not conversational", id="blob"),
        pytest.param([{"conversational": {"role": "TOOL", "content": {"text": "x"}}}], "role", id="tool-role"),
        pytest.param([{"conversational": {"role": "USER", "content": {}}}], "no text", id="empty-text"),
    ],
)
def test_unusable_payloads_are_dropped(
    client: MagicMock,
    memory: ConversationMemory,
    payload: list[dict[str, Any]],
    reason: str,
) -> None:
    client.list_events.return_value = {
        "events": [{"eventTimestamp": datetime(2026, 8, 11, tzinfo=UTC), "payload": payload}],
    }

    assert memory.load("alice", "session-1") == [], reason


def test_appending_stores_role_and_text(client: MagicMock, memory: ConversationMemory) -> None:
    memory.append("alice", "session-1", "assistant", "Added.")

    kwargs = client.create_event.call_args.kwargs
    assert kwargs["memoryId"] == "memory-test"
    assert kwargs["actorId"] == "alice"
    assert kwargs["sessionId"] == "session-1"
    assert kwargs["payload"] == [{"conversational": {"role": "ASSISTANT", "content": {"text": "Added."}}}]


def test_blank_turns_are_not_stored(client: MagicMock, memory: ConversationMemory) -> None:
    """A turn that produced only tool calls has no text worth billing for."""
    memory.append("alice", "session-1", "assistant", "   ")

    client.create_event.assert_not_called()

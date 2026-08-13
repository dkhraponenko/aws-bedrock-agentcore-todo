"""Conversation memory: ordering, role mapping and what is deliberately not stored."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from todo_runtime.memory import FAILED_TURN_NOTE, MAX_HISTORY_EVENTS, ConversationMemory, MemoryClient


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


@pytest.mark.parametrize("answered_at", [2, 1], ids=["later-minute", "same-instant"])
def test_history_is_returned_oldest_first(
    client: MagicMock,
    memory: ConversationMemory,
    answered_at: int,
) -> None:
    """ListEvents answers newest first; Converse needs the opposite.

    Two events can share a timestamp, so the order has to survive a sort that
    is free to treat them as equal.
    """
    client.list_events.return_value = {
        "events": [event("ASSISTANT", "answer", answered_at), event("USER", "question", 1)],
    }

    assert memory.load("alice", "session-1") == [
        {"role": "user", "content": [{"text": "question"}]},
        {"role": "assistant", "content": [{"text": "answer"}]},
    ]


def test_history_never_starts_with_an_assistant_turn(client: MagicMock, memory: ConversationMemory) -> None:
    """Converse requires the first message to be the user's, and the window can cut mid-turn."""
    client.list_events.return_value = {
        "events": [event("USER", "second question", 3), event("ASSISTANT", "orphaned answer", 2)],
    }

    # The trailing question is paired below; what this pins is that the answer
    # whose question fell off the window does not become the first message.
    assert memory.load("alice", "session-1") == [
        {"role": "user", "content": [{"text": "second question"}]},
        {"role": "assistant", "content": [{"text": FAILED_TURN_NOTE}]},
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


def test_an_unanswered_question_is_paired_with_a_note(client: MagicMock, memory: ConversationMemory) -> None:
    """The prompt is stored before the model runs, so a dead turn leaves it unanswered."""
    client.list_events.return_value = {"events": [event("USER", "delete the milk one", 1)]}

    assert memory.load("alice", "session-1") == [
        {"role": "user", "content": [{"text": "delete the milk one"}]},
        {"role": "assistant", "content": [{"text": FAILED_TURN_NOTE}]},
    ]


def test_an_unanswered_question_mid_history_is_paired(client: MagicMock, memory: ConversationMemory) -> None:
    """Converse rejects two user messages in a row, whichever turn died."""
    client.list_events.return_value = {
        "events": [event("ASSISTANT", "Deleted.", 3), event("USER", "try again", 2), event("USER", "delete it", 1)],
    }

    assert [message["role"] for message in memory.load("alice", "session-1")] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]

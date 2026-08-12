"""The runtime entry point: configuration, framing and failure containment."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from todo_runtime import entrypoint
from todo_runtime.entrypoint import AgentService, invoke


if TYPE_CHECKING:
    from collections.abc import Generator, Iterator


class FakeAgent:
    """Yields prepared events, or raises when asked to."""

    def __init__(self, events: list[dict[str, Any]] | None = None, error: Exception | None = None) -> None:
        self._events = events or []
        self._error = error
        self.calls: list[tuple[str, str, str]] = []

    def run(self, user_id: str, session_id: str, prompt: str) -> Iterator[dict[str, Any]]:
        self.calls.append((user_id, session_id, prompt))
        if self._error is not None:
            raise self._error
        yield from self._events


@pytest.fixture(autouse=True)
def restore_agent() -> Generator[None]:
    """The service holds one agent across invocations; keep tests independent."""
    original = AgentService._agent
    yield
    AgentService._agent = original


def frames(stream: Iterator[str]) -> list[dict[str, Any]]:
    """Decode server-sent event frames back into events."""
    return [json.loads(frame.removeprefix("data:").strip()) for frame in stream]


def test_setup_ran_at_import() -> None:
    """A missing setting must stop the deployment, not the first conversation."""
    assert AgentService._agent is not None


def test_setup_names_the_missing_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_ID")

    with pytest.raises(ValueError, match="MEMORY_ID"):
        AgentService.setup()


def test_uninitialised_service_refuses_to_run() -> None:
    AgentService._agent = None

    with pytest.raises(RuntimeError, match="not initialized"):
        list(AgentService.stream({"prompt": "hi"}))


def test_events_are_rendered_as_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = FakeAgent([{"type": "text", "text": "Hello."}, {"type": "done"}])
    monkeypatch.setattr(AgentService, "_agent", agent)

    stream = list(invoke({"prompt": "hi", "user_id": "alice", "session_id": "s-1"}))

    assert stream[0] == 'data: {"type": "text", "text": "Hello."}\n\n'
    assert agent.calls == [("alice", "s-1", "hi")]


def test_missing_identity_falls_back_without_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = FakeAgent([{"type": "done"}])
    monkeypatch.setattr(AgentService, "_agent", agent)

    list(invoke({"prompt": "hi"}))

    assert agent.calls == [(entrypoint.DEFAULT_USER_ID, entrypoint.DEFAULT_SESSION_ID, "hi")]


@pytest.mark.parametrize("payload", [{}, {"prompt": "   "}], ids=["absent", "blank"])
def test_an_empty_prompt_is_answered_not_raised(payload: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AgentService, "_agent", FakeAgent())

    assert frames(invoke(payload)) == [
        {"type": "error", "message": "Empty prompt."},
        {"type": "done"},
    ]


def test_a_failing_turn_still_closes_the_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client waits for `done`; an exception must not leave it hanging."""
    monkeypatch.setattr(AgentService, "_agent", FakeAgent(error=RuntimeError("gateway down")))

    assert frames(invoke({"prompt": "hi", "user_id": "alice"})) == [
        {"type": "error", "message": "RuntimeError: gateway down"},
        {"type": "done"},
    ]

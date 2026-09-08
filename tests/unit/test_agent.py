"""The agent loop: tool selection, identity injection, and the iteration cap."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any

import pytest

from todo_runtime.agent import USER_ID_ARGUMENT, AgentConfig, BedrockRuntime, TodoAgent
from todo_runtime.memory import ConversationMemory


TOOL_NAME = "todo___add_item"


def text_stream(text: str) -> list[dict[str, Any]]:
    """A model turn that answers and stops."""
    return [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]


def deltas(*chunks: str) -> list[dict[str, Any]]:
    """A model turn whose text is split across deltas at the given points."""
    return [
        *({"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": chunk}}} for chunk in chunks),
        {"messageStop": {"stopReason": "end_turn"}},
    ]


def tool_stream(arguments: str, tool_use_id: str = "tu-1", name: str = TOOL_NAME) -> list[dict[str, Any]]:
    """A model turn that calls one tool, with its arguments split across deltas."""
    half = len(arguments) // 2
    return [
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {"toolUseId": tool_use_id, "name": name}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": arguments[:half]}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": arguments[half:]}}}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]


class FakeBedrock:
    """Returns prepared streams in order and records every request."""

    def __init__(self, *streams: list[dict[str, Any]]) -> None:
        self._streams = list(streams)
        self.requests: list[dict[str, Any]] = []

    def converse_stream(self, **kwargs: Any) -> dict[str, Any]:
        # Snapshot, because the loop keeps appending to the same messages list:
        # a real client serialises the request here, and a fake that stored the
        # live reference would let every assertion see the final state instead.
        self.requests.append(deepcopy(kwargs))
        # Repeat the last stream once exhausted, so the iteration cap can be
        # driven without preparing one stream per allowed step.
        stream = self._streams.pop(0) if len(self._streams) > 1 else self._streams[0]
        return {"stream": stream}


class FakeGateway:
    """Publishes one tool and records the arguments it is called with."""

    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.list_calls = 0
        self.result = result if result is not None else {"created": True}

    def list_tools(self) -> list[dict[str, Any]]:
        self.list_calls += 1
        return [
            {
                "name": TOOL_NAME,
                "description": "Add a task.",
                "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        return self.result


class DictMemoryClient:
    """AgentCore Memory's two calls, backed by a list."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def create_event(self, **kwargs: Any) -> dict[str, Any]:
        self.events.append(dict(kwargs))
        return {"event": kwargs}

    def list_events(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "events": [
                event
                for event in self.events
                if event["actorId"] == kwargs["actorId"] and event["sessionId"] == kwargs["sessionId"]
            ],
        }


@pytest.fixture
def memory_client() -> DictMemoryClient:
    return DictMemoryClient()


def build_agent(
    bedrock: BedrockRuntime,
    gateway: FakeGateway,
    memory_client: DictMemoryClient,
    max_iterations: int = 10,
) -> TodoAgent:
    return TodoAgent(
        bedrock=bedrock,
        gateway=gateway,
        memory=ConversationMemory(memory_client, memory_id="memory-test"),
        config=AgentConfig(model_id="model-test", system_prompt="be helpful", max_iterations=max_iterations),
    )


def test_answers_without_tools_and_records_both_turns(memory_client: DictMemoryClient) -> None:
    agent = build_agent(FakeBedrock(text_stream("Hello.")), FakeGateway(), memory_client)

    events = list(agent.run("alice", "session-1", "hi"))

    assert {"type": "text", "text": "Hello."} in events
    assert events[-1] == {"type": "done"}
    stored = [event["payload"][0]["conversational"] for event in memory_client.events]
    assert stored == [
        {"role": "USER", "content": {"text": "hi"}},
        {"role": "ASSISTANT", "content": {"text": "Hello."}},
    ]


@pytest.mark.parametrize(
    "chunks",
    [
        pytest.param(("<thinking>I will add it.</thinking>Added.",), id="one-delta"),
        pytest.param(("<thin", "king>I will", " add it.</thin", "king>Added."), id="tags-split-across-deltas"),
        pytest.param(("<thinking>I will add it.</thinking>", "Added."), id="answer-in-its-own-delta"),
    ],
)
def test_the_models_reasoning_never_reaches_the_user(
    memory_client: DictMemoryClient,
    chunks: tuple[str, ...],
) -> None:
    """Nova writes <thinking> into ordinary text, split wherever the stream splits."""
    agent = build_agent(FakeBedrock(deltas(*chunks)), FakeGateway(), memory_client)

    events = list(agent.run("alice", "session-1", "add buy a milk"))

    spoken = "".join(event["text"] for event in events if event["type"] == "text")
    assert spoken == "Added."
    # And it is not replayed to the model, or paid for, on every later turn.
    stored = [event["payload"][0]["conversational"] for event in memory_client.events]
    assert stored[-1] == {"role": "ASSISTANT", "content": {"text": "Added."}}


def test_text_that_only_resembles_a_tag_is_still_shown(memory_client: DictMemoryClient) -> None:
    """The held-back tail has to be released once no delta can complete a tag."""
    agent = build_agent(FakeBedrock(deltas("Done, 2 items <", "3 left.")), FakeGateway(), memory_client)

    events = list(agent.run("alice", "session-1", "hi"))

    assert "".join(event["text"] for event in events if event["type"] == "text") == "Done, 2 items <3 left."


def test_a_message_ending_mid_tag_still_shows_its_last_character(memory_client: DictMemoryClient) -> None:
    """Nothing more can arrive to complete the tag, so the tail is text after all."""
    agent = build_agent(FakeBedrock(deltas("All done <")), FakeGateway(), memory_client)

    events = list(agent.run("alice", "session-1", "hi"))

    assert "".join(event["text"] for event in events if event["type"] == "text") == "All done <"


def test_an_unclosed_thinking_block_shows_nothing_and_says_so(
    memory_client: DictMemoryClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Swallowing an answer and swallowing reasoning look identical from here."""
    agent = build_agent(FakeBedrock(deltas("<thinking>I am not finished")), FakeGateway(), memory_client)

    with caplog.at_level("WARNING", logger="todo_runtime.agent"):
        events = list(agent.run("alice", "session-1", "hi"))

    assert [event for event in events if event["type"] == "text"] == []
    assert "unclosed" in caplog.text


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({"text": "buy a milk"}, id="model-supplied-none"),
        pytest.param({"text": "buy a milk", USER_ID_ARGUMENT: "victim"}, id="model-supplied-another-user"),
    ],
)
def test_tool_call_carries_the_verified_user_id(
    memory_client: DictMemoryClient,
    arguments: dict[str, Any],
) -> None:
    """The whole point of owning the loop: identity the model never chose.

    A model talked into naming another user must not reach the tool with it, so
    the injected value is merged last rather than defaulted in.
    """
    gateway = FakeGateway()
    bedrock = FakeBedrock(tool_stream(json.dumps(arguments)), text_stream("Added."))

    list(build_agent(bedrock, gateway, memory_client).run("alice", "session-1", "add buy a milk"))

    assert gateway.calls == [(TOOL_NAME, {"text": "buy a milk", USER_ID_ARGUMENT: "alice"})]


def test_tool_result_is_fed_back_to_the_model(memory_client: DictMemoryClient) -> None:
    gateway = FakeGateway(result={"created": True, "item": {"item_id": "abc"}})
    bedrock = FakeBedrock(tool_stream(json.dumps({"text": "x"})), text_stream("Done."))

    events = list(build_agent(bedrock, gateway, memory_client).run("alice", "session-1", "add x"))

    # Verify: the second request replays the call and its result as a pair.
    second_turn = bedrock.requests[1]["messages"]
    assert second_turn[-2]["content"][0]["toolUse"]["toolUseId"] == "tu-1"
    assert second_turn[-1]["content"][0]["toolResult"] == {
        "toolUseId": "tu-1",
        "content": [{"json": {"created": True, "item": {"item_id": "abc"}}}],
    }
    assert {"type": "tool_result", "name": TOOL_NAME, "result": gateway.result} in events


def test_text_alongside_a_tool_call_is_kept_in_order(memory_client: DictMemoryClient) -> None:
    """Models narrate while calling a tool; both blocks have to survive the replay."""
    gateway = FakeGateway()
    mixed: list[dict[str, Any]] = [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Let me check. "}}},
        {"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolUse": {"toolUseId": "tu-1", "name": TOOL_NAME}}}},
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"text": "x"}'}}}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]
    bedrock = FakeBedrock(mixed, text_stream("Added."))

    events = list(build_agent(bedrock, gateway, memory_client).run("alice", "session-1", "add x"))

    replayed = bedrock.requests[1]["messages"][-2]["content"]
    assert replayed[0] == {"text": "Let me check. "}
    assert replayed[1]["toolUse"]["name"] == TOOL_NAME
    assert len(gateway.calls) == 1
    # Only the last pass is the answer: "Let me check. " was the model narrating
    # before a tool call, and replaying it as something it said would be wrong.
    assert memory_client.events[-1]["payload"][0]["conversational"]["content"]["text"] == "Added."
    assert {"type": "text", "text": "Let me check. "} in events


def test_iteration_cap_ends_the_turn_with_an_error(memory_client: DictMemoryClient) -> None:
    bedrock = FakeBedrock(tool_stream(json.dumps({"text": "x"})))

    events = list(build_agent(bedrock, FakeGateway(), memory_client, max_iterations=3).run("a", "s", "loop"))

    assert len(bedrock.requests) == 3
    assert events[-2] == {"type": "error", "message": "Stopped after 3 steps without finishing."}
    assert events[-1] == {"type": "done"}


def test_unparseable_tool_arguments_become_an_empty_call(memory_client: DictMemoryClient) -> None:
    """A truncated argument stream should let the tool report what is missing."""
    gateway = FakeGateway()
    bedrock = FakeBedrock(tool_stream('{"text": "unterminated'), text_stream("Sorry."))

    list(build_agent(bedrock, gateway, memory_client).run("alice", "session-1", "add x"))

    assert gateway.calls[0][1] == {USER_ID_ARGUMENT: "alice"}


def test_history_precedes_the_new_prompt(memory_client: DictMemoryClient) -> None:
    bedrock = FakeBedrock(text_stream("first"), text_stream("second"))
    agent = build_agent(bedrock, FakeGateway(), memory_client)

    list(agent.run("alice", "session-1", "one"))
    list(agent.run("alice", "session-1", "two"))

    assert bedrock.requests[1]["messages"] == [
        {"role": "user", "content": [{"text": "one"}]},
        {"role": "assistant", "content": [{"text": "first"}]},
        {"role": "user", "content": [{"text": "two"}]},
    ]


class ExplodingBedrock:
    """Fails the way a throttled or unreachable model does."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.requests: list[dict[str, Any]] = []

    def converse_stream(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(dict(kwargs))
        raise self._error


def test_a_failed_turn_still_records_the_question(memory_client: DictMemoryClient) -> None:
    """History that silently drops a turn leaves the retry without the context."""
    agent = build_agent(ExplodingBedrock(RuntimeError("throttled")), FakeGateway(), memory_client)

    with pytest.raises(RuntimeError, match="throttled"):
        list(agent.run("alice", "session-1", "delete the milk one"))

    stored = [event["payload"][0]["conversational"] for event in memory_client.events]
    assert stored == [{"role": "USER", "content": {"text": "delete the milk one"}}]


def test_a_disconnected_client_still_records_the_question(memory_client: DictMemoryClient) -> None:
    """Abandoning the stream closes the generator, which is not an Exception."""
    agent = build_agent(FakeBedrock(text_stream("Hello.")), FakeGateway(), memory_client)

    turn = agent.run("alice", "session-1", "add buy milk")
    assert next(turn) == {"type": "text", "text": "Hello."}
    turn.close()

    stored = [event["payload"][0]["conversational"] for event in memory_client.events]
    assert stored == [{"role": "USER", "content": {"text": "add buy milk"}}]


def test_a_failed_turn_keeps_history_in_alternating_pairs(memory_client: DictMemoryClient) -> None:
    """Converse rejects two user messages in a row, which a bare prompt would create."""
    failing = build_agent(ExplodingBedrock(RuntimeError("throttled")), FakeGateway(), memory_client)
    with pytest.raises(RuntimeError, match="throttled"):
        list(failing.run("alice", "session-1", "delete the milk one"))

    retry = build_agent(FakeBedrock(text_stream("Deleted.")), FakeGateway(), memory_client)
    list(retry.run("alice", "session-1", "try again"))

    history = ConversationMemory(memory_client, memory_id="memory-test").load("alice", "session-1")

    assert [message["role"] for message in history] == ["user", "assistant", "user", "assistant"]


class SlowGateway(FakeGateway):
    """Pauses inside list_tools so concurrent turns overlap in the cache."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.list_started = 0

    def list_tools(self) -> list[dict[str, Any]]:
        with self._lock:
            self.list_started += 1
        time.sleep(0.05)
        return super().list_tools()


def test_the_tool_config_is_fetched_once_under_concurrent_turns(memory_client: DictMemoryClient) -> None:
    """The agent is shared across turns; its lazy cache must not be filled twice."""
    gateway = SlowGateway()
    agent = build_agent(FakeBedrock(text_stream("Hi.")), gateway, memory_client)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda n: list(agent.run("alice", f"s-{n}", "hi")), range(6)))

    assert gateway.list_started == 1


def test_history_is_not_shared_between_users(memory_client: DictMemoryClient) -> None:
    bedrock = FakeBedrock(text_stream("first"), text_stream("second"))
    agent = build_agent(bedrock, FakeGateway(), memory_client)

    list(agent.run("alice", "session-1", "alice's secret"))
    list(agent.run("bob", "session-1", "bob's turn"))

    assert bedrock.requests[1]["messages"] == [{"role": "user", "content": [{"text": "bob's turn"}]}]

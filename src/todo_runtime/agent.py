"""The agent loop: model, tools, memory.

Owning the loop is what makes per-user isolation possible: the identity arrives
with the turn and is injected into every tool call here, never chosen by the
model. Who may supply it is the trust boundary in `todo_runtime.entrypoint`.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol


if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

    from todo_runtime.memory import ConversationMemory


logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10

# Injected into every tool call. The gateway does not publish it as a parameter,
# so the model neither sees it nor can supply one that survives the merge.
USER_ID_ARGUMENT = "user_id"


class BedrockRuntime(Protocol):
    """The single Bedrock call the loop makes."""

    def converse_stream(self, **kwargs: Any) -> dict[str, Any]: ...  # noqa: ANN401


class ToolGateway(Protocol):
    """The two gateway calls the loop makes.

    Structural so the in-process driver in scripts/ can stand in for the client.
    """

    def list_tools(self) -> list[dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AgentConfig:
    """How the agent reasons, as opposed to what it talks to."""

    model_id: str
    system_prompt: str
    max_iterations: int = MAX_ITERATIONS


class _ThinkingFilter:
    """Removes the model's reasoning from a stream of text deltas.

    Nova writes `<thinking>...</thinking>` into ordinary content, and the tags
    arrive split across deltas, so the tail that could still become a tag is
    held back until the next delta decides it.
    """

    _OPEN = "<thinking>"
    _CLOSE = "</thinking>"

    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False

    def feed(self, text: str) -> str:
        """Return the part of this delta the user should see, which may be none."""
        self._buffer += text
        visible: list[str] = []

        while True:
            marker = self._CLOSE if self._inside else self._OPEN
            found = self._buffer.find(marker)
            if found >= 0:
                if not self._inside:
                    visible.append(self._buffer[:found])
                self._buffer = self._buffer[found + len(marker) :]
                self._inside = not self._inside
                continue

            # No whole tag: keep what could still grow into one, and let the
            # rest through — or drop it, if this is reasoning.
            keep = _partial_tag_length(self._buffer, marker)
            if not self._inside:
                visible.append(self._buffer[: len(self._buffer) - keep])
            self._buffer = self._buffer[len(self._buffer) - keep :]
            break

        return "".join(visible)

    def flush(self) -> str:
        """Return whatever the end of the stream leaves held back."""
        remainder, self._buffer = self._buffer, ""
        if self._inside:
            # Everything after an unclosed tag is reasoning, so there is nothing
            # to show. Logged: a swallowed answer looks the same from here.
            logger.warning("Model left a thinking block unclosed")
            return ""
        return remainder


def _partial_tag_length(text: str, tag: str) -> int:
    """How many characters at the end of `text` could still become `tag`."""
    for length in range(min(len(text), len(tag) - 1), 0, -1):
        if text.endswith(tag[:length]):
            return length
    return 0


class _StreamedMessage:
    """Reassembles one Converse stream into a message.

    Text arrives as deltas and a tool call as a name followed by JSON fragments,
    both keyed by content block index, and each has to be concatenated first.
    """

    def __init__(self) -> None:
        self._text: dict[int, list[str]] = {}
        self._tools: dict[int, dict[str, Any]] = {}
        self._thinking = _ThinkingFilter()
        self._text_index = 0
        self.stop_reason = ""

    def consume(self, event: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Absorb one stream event, yielding whatever the caller should show."""
        if start := event.get("contentBlockStart"):
            if tool := start.get("start", {}).get("toolUse"):
                self._tools[start["contentBlockIndex"]] = {
                    "toolUseId": tool["toolUseId"],
                    "name": tool["name"],
                    "fragments": [],
                }
                yield {"type": "tool_use", "name": tool["name"]}

        elif delta_event := event.get("contentBlockDelta"):
            index = delta_event["contentBlockIndex"]
            delta = delta_event["delta"]
            if (text := delta.get("text")) is not None:
                self._text_index = index
                if visible := self._thinking.feed(text):
                    self._text.setdefault(index, []).append(visible)
                    yield {"type": "text", "text": visible}
            elif (tool_delta := delta.get("toolUse")) and index in self._tools:
                self._tools[index]["fragments"].append(tool_delta.get("input", ""))

        elif stop := event.get("messageStop"):
            self.stop_reason = stop["stopReason"]
            # Held-back text is only known to be text once no more deltas can
            # turn it into a tag, and that is here.
            if remainder := self._thinking.flush():
                self._text.setdefault(self._text_index, []).append(remainder)
                yield {"type": "text", "text": remainder}

    @property
    def content(self) -> list[dict[str, Any]]:
        """The assembled content blocks, in the order the model emitted them."""
        blocks: dict[int, dict[str, Any]] = {index: {"text": "".join(parts)} for index, parts in self._text.items()}
        for index, tool in self._tools.items():
            blocks[index] = {
                "toolUse": {
                    "toolUseId": tool["toolUseId"],
                    "name": tool["name"],
                    "input": _parse_arguments(tool["name"], tool["fragments"]),
                },
            }
        return [blocks[index] for index in sorted(blocks)]


def _parse_arguments(tool_name: str, fragments: list[str]) -> dict[str, Any]:
    """Join streamed argument fragments into a dict.

    Malformed JSON becomes empty arguments rather than an exception: the tool
    then names the missing parameter, which the model can act on.
    """
    raw = "".join(fragments).strip() or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Model emitted unparseable tool arguments", extra={"tool": tool_name})
        return {}
    return parsed if isinstance(parsed, dict) else {}


class TodoAgent:
    """Runs one user turn to completion against the model and the gateway."""

    def __init__(
        self,
        bedrock: BedrockRuntime,
        gateway: ToolGateway,
        memory: ConversationMemory,
        config: AgentConfig,
    ) -> None:
        self._bedrock = bedrock
        self._gateway = gateway
        self._memory = memory
        self._config = config
        self._system = [{"text": config.system_prompt}]
        self._tool_config: dict[str, Any] | None = None
        # One agent serves every turn, so its lazy cache is shared state.
        self._tool_config_lock = threading.Lock()

    def tool_config(self) -> dict[str, Any]:
        """Return the Converse tool config, fetched from the gateway once.

        The gateway is the authority on what it accepts, so the contract is read
        from it rather than from a copy of `tools.json`.
        """
        # Checked under the lock, not before it: two turns would otherwise both
        # find the cache unset and fetch the contract twice.
        with self._tool_config_lock:
            if self._tool_config is None:
                self._tool_config = {
                    "tools": [
                        {
                            "toolSpec": {
                                "name": tool["name"],
                                "description": tool.get("description", ""),
                                "inputSchema": {"json": tool["inputSchema"]},
                            },
                        }
                        for tool in self._gateway.list_tools()
                    ],
                }
            return self._tool_config

    def run(self, user_id: str, session_id: str, prompt: str) -> Generator[dict[str, Any]]:
        """Answer one user message, yielding events as they happen.

        `user_id` is taken from the payload, not established here, and partitions
        both memory and the table.

        Yields:
            `text`, `tool_use`, `tool_result`, `error` and a final `done` event.

        Raises:
            Exception: Whatever the model or a tool raised; the entry point
                renders it, keeping that decision in one place.
        """
        messages = [
            *self._memory.load(user_id, session_id),
            {"role": "user", "content": [{"text": prompt}]},
        ]

        # Stored before the model runs: everything below can end early, and the
        # question is the one part that cannot be reconstructed. `load` fills in
        # the missing answer.
        self._memory.append(user_id, session_id, "user", prompt)
        answer = ""

        for _ in range(self._config.max_iterations):
            # Each pass replaces the answer: earlier text is the model
            # narrating before a tool call, not something it said.
            spoken: list[str] = []
            streamed = _StreamedMessage()

            response = self._bedrock.converse_stream(
                modelId=self._config.model_id,
                system=self._system,
                messages=messages,
                toolConfig=self.tool_config(),
            )
            for event in response["stream"]:
                for update in streamed.consume(event):
                    if update["type"] == "text":
                        spoken.append(update["text"])
                    yield update

            answer = "".join(spoken)
            messages.append({"role": "assistant", "content": streamed.content})
            if streamed.stop_reason != "tool_use":
                break

            yield from self._run_tools(user_id, streamed.content, messages)
        else:
            cap = self._config.max_iterations
            logger.warning("Turn hit the iteration cap", extra={"user_id": user_id, "cap": cap})
            yield {"type": "error", "message": f"Stopped after {cap} steps without finishing."}

        self._memory.append(user_id, session_id, "assistant", answer)
        yield {"type": "done"}

    def _run_tools(
        self,
        user_id: str,
        content: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        """Invoke every tool the model asked for, appending the results as one turn."""
        results = []
        for block in content:
            if (tool := block.get("toolUse")) is None:
                continue
            result = self._invoke(user_id, tool)
            yield {"type": "tool_result", "name": tool["name"], "result": result}
            results.append({"toolResult": {"toolUseId": tool["toolUseId"], "content": [{"json": result}]}})
        messages.append({"role": "user", "content": results})

    def _invoke(self, user_id: str, tool: dict[str, Any]) -> dict[str, Any]:
        """Call one tool with the caller's identity forced into its arguments.

        The tool rejects a call without one, so a loop that stopped injecting it
        would fail loudly rather than quietly share a list.
        """
        arguments = {**tool["input"], USER_ID_ARGUMENT: user_id}
        logger.info("Calling tool", extra={"tool": tool["name"], "user_id": user_id})
        return self._gateway.call_tool(tool["name"], arguments)

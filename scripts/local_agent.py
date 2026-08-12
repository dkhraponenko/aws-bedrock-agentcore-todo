#!/usr/bin/env python3
"""Run the deployed agent loop locally: real model, nothing deployed.

Drives the same `TodoAgent` AgentCore Runtime executes, through the seams it
already has: the gateway becomes a local dispatcher into the tool Lambda, and
memory a dictionary. The loop itself is not reimplemented.

    AWS_PROFILE=personal python scripts/local_agent.py

Costs a few cents per conversation. DynamoDB is moto in-process, with Bedrock
allowed through by URL, since moto otherwise intercepts the model call too.
It does not cover the MCP transport or AgentCore's session lifecycle.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any


# src/ is a source root, not an installed package: the artifact is the source
# tree itself, so there is no editable install to lean on.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Set before todo_agent is imported: the handler builds its store at import
# time, and boto3 refuses to create a client without a region.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from moto import mock_aws

from todo_agent.lambda_handler import lambda_handler
from todo_agent.service import TodoService
from todo_agent.store import TodoStore
from todo_runtime.agent import AgentConfig, TodoAgent
from todo_runtime.memory import ConversationMemory


if TYPE_CHECKING:
    from moto.core.config import DefaultConfig


DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

INFRASTRUCTURE = Path(__file__).resolve().parent.parent / "infrastructure"
TOOLS_FILE = INFRASTRUCTURE / "tools.json"
INSTRUCTION_FILE = INFRASTRUCTURE / "agent_instruction.md"

TABLE_NAME = "todo-agent-items-local"
TARGET_NAME = "todo"
TOOL_SEPARATOR = "___"
DEFAULT_MODEL = "us.amazon.nova-pro-v1:0"
RESULT_PREVIEW_CHARS = 200

# Everything else is mocked; only the model call leaves this machine.
MOTO_CONFIG: DefaultConfig = {"core": {"passthrough": {"urls": [r"https://bedrock-runtime\..*"]}}}


class LocalGateway:
    """Stands in for the MCP gateway by calling the tool Lambda in-process.

    Tool names carry the same `<target>___<tool>` prefix the real gateway
    publishes, so the handler's parsing is exercised rather than bypassed.
    """

    def __init__(self, target: str = TARGET_NAME) -> None:
        self._target = target
        self._tools = json.loads(TOOLS_FILE.read_text())

    def list_tools(self) -> list[dict[str, Any]]:
        """Publish tools.json in the shape the gateway would return them."""
        return [
            {
                "name": f"{self._target}___{tool['name']}",
                "description": tool["description"],
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        prop["name"]: {"type": prop["type"], "description": prop["description"]}
                        for prop in tool["properties"]
                    },
                    "required": [prop["name"] for prop in tool["properties"] if prop["required"]],
                },
            }
            for tool in self._tools
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke the handler exactly as the gateway would."""
        # The real gateway refuses a tool it does not publish, and a local run
        # must not be more forgiving than production.
        if not name.startswith(f"{self._target}{TOOL_SEPARATOR}"):
            msg = f"Tool {name!r} is not published by target {self._target!r}."
            raise ValueError(msg)
        return lambda_handler(arguments, _FakeLambdaContext(name))


class _FakeClientContext:
    def __init__(self, tool: str) -> None:
        self.custom = {"bedrockAgentCoreToolName": tool}


class _FakeLambdaContext:
    def __init__(self, tool: str) -> None:
        self.client_context = _FakeClientContext(tool)


class DictMemoryClient:
    """AgentCore Memory's two calls, backed by a list that dies with the process."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def create_event(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self._events.append(dict(kwargs))
        return {"event": kwargs}

    def list_events(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        matching = [
            event
            for event in self._events
            if event["actorId"] == kwargs["actorId"] and event["sessionId"] == kwargs["sessionId"]
        ]
        return {"events": matching[-kwargs.get("maxResults", len(matching)) :]}


def bedrock_client() -> Any:  # noqa: ANN401
    """Build a Bedrock client that survives moto's fake credentials.

    `mock_aws` overwrites the credential environment variables, so the real
    ones are frozen here — before the mock starts — and passed explicitly.
    """
    frozen = boto3.Session().get_credentials().get_frozen_credentials()
    return boto3.client(
        "bedrock-runtime",
        aws_access_key_id=frozen.access_key,
        aws_secret_access_key=frozen.secret_key,
        aws_session_token=frozen.token,
        config=Config(connect_timeout=5, read_timeout=120, retries={"max_attempts": 2}),
    )


def wire_store() -> None:
    """Create the table in moto and point the handler's store at it."""
    client = boto3.client("dynamodb")
    client.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "user_id", "KeyType": "HASH"},
            {"AttributeName": "item_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "item_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    TodoService.setup(TodoStore(table_name=TABLE_NAME, dynamodb_client=client))


def render(event: dict[str, Any]) -> None:
    """Print one event from the agent's stream."""
    kind = event.get("type")
    if kind == "text":
        sys.stdout.write(event["text"])
        sys.stdout.flush()
    elif kind == "tool_use":
        print(f"\n{DIM}  -> {event['name']}{RESET}")
    elif kind == "tool_result":
        print(f"{DIM}     {json.dumps(event['result'], ensure_ascii=False)[:RESULT_PREVIEW_CHARS]}{RESET}")
    elif kind == "error":
        print(f"\n[error] {event.get('message', '')}")


def converse_until_eof(agent: TodoAgent, user_id: str, session_id: str) -> None:
    """Read prompts until EOF, keeping one session so memory accumulates."""
    while True:
        try:
            prompt = input(f"\n{BOLD}you >{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not prompt:
            continue

        print(f"{BOLD}agent >{RESET} ", end="")
        try:
            for event in agent.run(user_id, session_id, prompt):
                render(event)
        except ClientError as e:
            print(f"\n{e.response.get('Error', {}).get('Code', 'Unknown')}: {e}")
        print()


def main() -> int:
    """Wire the stand-ins and talk to the real model until EOF."""
    logging.getLogger("todo_agent").setLevel(logging.CRITICAL)
    logging.getLogger("todo_runtime").setLevel(logging.CRITICAL)

    model_id = os.environ.get("AGENT_MODEL", DEFAULT_MODEL)
    user_id = os.environ.get("USER_ID", "local-operator")
    client = bedrock_client()

    with mock_aws(config=MOTO_CONFIG):
        wire_store()
        agent = TodoAgent(
            bedrock=client,
            gateway=LocalGateway(),
            memory=ConversationMemory(DictMemoryClient(), memory_id="local"),
            config=AgentConfig(
                model_id=model_id,
                system_prompt=INSTRUCTION_FILE.read_text(),
            ),
        )
        session_id = str(uuid.uuid4())
        print(f"{BOLD}{model_id}{RESET}  user {user_id}  session {session_id}  (Ctrl-D to exit)")
        print(f"{DIM}DynamoDB is moto in-process; nothing is deployed{RESET}")
        converse_until_eof(agent, user_id, session_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())

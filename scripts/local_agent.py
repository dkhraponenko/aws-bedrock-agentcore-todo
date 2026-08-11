#!/usr/bin/env python3
"""Run the agent loop locally: real model, real tool schemas, nothing deployed.

Stands in for the AgentCore harness with a Bedrock Converse tool-use loop. The
tool contract comes from infrastructure/tools.json and the system prompt from
infrastructure/agent_instruction.md — the same bytes Terraform publishes — so
this exercises what unit tests cannot: whether the model picks the right tool
and resolves wording to an item_id before deleting.

    AWS_PROFILE=personal PYTHONPATH=src python scripts/local_agent.py

Needs credentials and costs a few cents per conversation; it deploys nothing.
DynamoDB is moto in-process, with Bedrock allowed through by URL, because moto
otherwise intercepts every AWS call — including the one to the model.

What it does not cover: AgentCore's own orchestration — iteration limits,
memory, the gateway's MCP layer. Those exist only once the stack is applied.
The default model id is duplicated from infrastructure/variables.tf; one
environment variable is not worth a shared file.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol


os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from moto import mock_aws

from todo_agent.lambda_handler import lambda_handler
from todo_agent.service import TodoService
from todo_agent.store import TodoStore


if TYPE_CHECKING:
    from moto.core.config import DefaultConfig


DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

INFRASTRUCTURE = Path(__file__).resolve().parent.parent / "infrastructure"
TOOLS_FILE = INFRASTRUCTURE / "tools.json"
INSTRUCTION_FILE = INFRASTRUCTURE / "agent_instruction.md"

TABLE_NAME = "todo-agent-items-local"
DEFAULT_MODEL = "us.amazon.nova-pro-v1:0"
MAX_ITERATIONS = 10
RESULT_PREVIEW_CHARS = 200

# Everything else is mocked; only the model call leaves this machine.
MOTO_CONFIG: DefaultConfig = {"core": {"passthrough": {"urls": [r"https://bedrock-runtime\..*"]}}}


class BedrockRuntime(Protocol):
    """The single Bedrock call this script makes."""

    def converse(self, **kwargs: Any) -> dict[str, Any]: ...  # noqa: ANN401


class FakeClientContext:
    """Stands in for the Lambda client context AgentCore populates."""

    def __init__(self, tool: str) -> None:
        self.custom = {"bedrockAgentCoreToolName": f"todo___{tool}"}


class FakeLambdaContext:
    """The only attribute the handler reads off the AWS-supplied context."""

    def __init__(self, tool: str) -> None:
        self.client_context = FakeClientContext(tool)


def load_tool_config() -> dict[str, Any]:
    """Translate tools.json into the toolConfig shape Converse expects."""
    tools = json.loads(TOOLS_FILE.read_text())
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                prop["name"]: {"type": prop["type"], "description": prop["description"]}
                                for prop in tool["properties"]
                            },
                            "required": [prop["name"] for prop in tool["properties"] if prop["required"]],
                        },
                    },
                },
            }
            for tool in tools
        ],
    }


def bedrock_client() -> BedrockRuntime:
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


def run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute one tool call through the real Lambda entry point."""
    print(f"\n{DIM}  -> {name} {json.dumps(arguments, ensure_ascii=False)}{RESET}")
    result = lambda_handler(arguments, FakeLambdaContext(name))

    rendered = json.dumps(result, ensure_ascii=False)
    if len(rendered) > RESULT_PREVIEW_CHARS:
        rendered = rendered[:RESULT_PREVIEW_CHARS] + "…"
    print(f"{DIM}     {rendered}{RESET}")
    return result


def turn(
    client: BedrockRuntime,
    model_id: str,
    system: list[dict[str, str]],
    tool_config: dict[str, Any],
    messages: list[dict[str, Any]],
) -> None:
    """Run one user turn to completion, executing tools until the model stops."""
    for _ in range(MAX_ITERATIONS):
        response = client.converse(
            modelId=model_id,
            system=system,
            messages=messages,
            toolConfig=tool_config,
        )
        message = response["output"]["message"]
        messages.append(message)

        for block in message["content"]:
            if text := block.get("text"):
                print(text.strip())

        if response["stopReason"] != "tool_use":
            return

        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": block["toolUse"]["toolUseId"],
                            "content": [{"json": run_tool(block["toolUse"]["name"], block["toolUse"]["input"])}],
                        },
                    }
                    for block in message["content"]
                    if "toolUse" in block
                ],
            }
        )

    print(f"{DIM}[stopped: reached {MAX_ITERATIONS} iterations]{RESET}")


def converse_until_eof(client: BedrockRuntime, model_id: str) -> None:
    """Read prompts until EOF, keeping one message history for the session."""
    system = [{"text": INSTRUCTION_FILE.read_text()}]
    tool_config = load_tool_config()
    messages: list[dict[str, Any]] = []

    while True:
        try:
            prompt = input(f"\n{BOLD}you >{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not prompt:
            continue

        messages.append({"role": "user", "content": [{"text": prompt}]})
        print(f"{BOLD}agent >{RESET} ", end="")
        try:
            turn(client, model_id, system, tool_config, messages)
        except ClientError as e:
            print(f"\n{e.response.get('Error', {}).get('Code', 'Unknown')}: {e}")


def main() -> int:
    """Wire the mocked table and talk to the real model until EOF."""
    logging.getLogger("todo_agent").setLevel(logging.CRITICAL)

    model_id = os.environ.get("AGENT_MODEL", DEFAULT_MODEL)
    client = bedrock_client()

    with mock_aws(config=MOTO_CONFIG):
        wire_store()
        print(f"{BOLD}{model_id}{RESET}  session {uuid.uuid4()}  (Ctrl-D to exit)")
        print(f"{DIM}DynamoDB is moto in-process; nothing is deployed{RESET}")
        converse_until_eof(client, model_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())

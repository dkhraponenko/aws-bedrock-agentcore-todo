#!/usr/bin/env python3
"""Drive the Lambda through a simulated gateway invocation, entirely offline.

No credentials, no deployed infrastructure: DynamoDB is moto, and the event and
client context are built exactly as AgentCore Gateway would send them. What it
proves is the half of the system the unit tests do not reach end to end — the
invocation contract, dispatch, validation and persistence in one pass.

    python scripts/local_invoke.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# src/ is a source root, not an installed package: the deployment artifact is
# the source tree itself, so there is no editable install to lean on.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Set before todo_agent is imported: the handler builds its store at import
# time, and boto3 refuses to create a client without a region.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

import boto3
from moto import mock_aws

from todo_agent.lambda_handler import lambda_handler
from todo_agent.service import TodoService
from todo_agent.store import TodoStore


DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

TABLE_NAME = "todo-agent-items-local"
TARGET_NAME = "todo"
RESULT_PREVIEW_CHARS = 200


@dataclass
class FakeClientContext:
    """Stands in for the Lambda client context AgentCore populates."""

    custom: dict[str, str]


@dataclass
class FakeLambdaContext:
    """The only attribute the handler reads off the AWS-supplied context."""

    client_context: FakeClientContext


def invoke(tool: str, **arguments: Any) -> dict[str, Any]:  # noqa: ANN401
    """Call the handler the way the gateway does and print the exchange."""
    context = FakeLambdaContext(FakeClientContext({"bedrockAgentCoreToolName": f"{TARGET_NAME}___{tool}"}))
    result = lambda_handler(arguments, context)

    rendered = json.dumps(result, ensure_ascii=False)
    if len(rendered) > RESULT_PREVIEW_CHARS:
        rendered = rendered[:RESULT_PREVIEW_CHARS] + "…"
    print(f"  -> {tool} {json.dumps(arguments, ensure_ascii=False)}")
    print(f"{DIM}     {rendered}{RESET}")
    return result


def create_table(client: Any) -> None:  # noqa: ANN401
    """Create the table with the schema from infrastructure/dynamodb.tf."""
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


def happy_path() -> None:
    """The sequence the agent produces for a delete phrased in natural language."""
    print(f"\n{BOLD}happy path{RESET}")
    invoke("add_item", text="buy a milk")
    invoke("add_item", text="call the dentist", priority=1)

    found = invoke("search_items", query="milk")
    invoke("delete_item", item_id=found["items"][0]["item_id"])
    invoke("list_items")


def rejected_inputs() -> None:
    """Every one of these must come back as a readable error, never a traceback."""
    print(f"\n{BOLD}rejected inputs{RESET}")
    invoke("add_item", text="   ")
    invoke("add_item", text="somewhere", priority=99)
    invoke("update_item", item_id="19fe7ec61e693a7fd")
    invoke("delete_item", item_id="does-not-exist")
    invoke("wipe_everything")


def main() -> int:
    """Run both sequences against a fresh moto-backed table."""
    # The handler logs every rejection; the returned error says the same thing
    # in order, so keep the transcript readable.
    logging.getLogger("todo_agent").setLevel(logging.CRITICAL)

    with mock_aws():
        client = boto3.client("dynamodb", region_name=os.environ["AWS_DEFAULT_REGION"])
        create_table(client)

        # Inject the mocked client through the same seam the tests use, so the
        # store built at import time is not the one doing the work.
        TodoService.setup(TodoStore(table_name=TABLE_NAME, dynamodb_client=client))

        happy_path()
        rejected_inputs()

    print(f"\n{DIM}source: {Path(__file__).name} — no AWS calls left this machine{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

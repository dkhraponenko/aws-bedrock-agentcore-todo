"""The tool contract Terraform publishes must match what the Lambda implements."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3
import pytest
from moto import mock_aws

from tests.conftest import TABLE_NAME, create_invocation
from todo_agent.lambda_handler import lambda_handler
from todo_agent.service import TOOL_NAMES, TodoService
from todo_agent.store import TodoStore


if TYPE_CHECKING:
    from collections.abc import Generator


TOOLS_FILE = Path(__file__).resolve().parents[2] / "infrastructure" / "tools.json"
TOOLS: list[dict[str, Any]] = json.loads(TOOLS_FILE.read_text())

ALLOWED_TYPES = {"string", "integer"}
SAMPLE_VALUES: dict[str, Any] = {"string": "x", "integer": 1}


def required_names(tool: dict[str, Any]) -> list[str]:
    return [prop["name"] for prop in tool["properties"] if prop["required"]]


@pytest.fixture
def wired_store(aws_credentials: None) -> Generator[None]:
    """Back the handler with a moto table so tool calls reach real validation."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
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
        yield


def test_declared_tools_match_implemented_handlers() -> None:
    assert {tool["name"] for tool in TOOLS} == set(TOOL_NAMES)


@pytest.mark.parametrize("tool", TOOLS, ids=lambda tool: str(tool["name"]))
def test_every_tool_declares_usable_properties(tool: dict[str, Any]) -> None:
    assert tool["description"].strip()
    assert tool["properties"], "a tool with no properties cannot be called usefully"

    names = [prop["name"] for prop in tool["properties"]]
    assert len(names) == len(set(names))
    for prop in tool["properties"]:
        assert prop["type"] in ALLOWED_TYPES
        assert prop["description"].strip()
        assert isinstance(prop["required"], bool)


@pytest.mark.parametrize(
    ("tool_name", "omitted"),
    [
        pytest.param(tool["name"], name, id=f"{tool['name']}-without-{name}")
        for tool in TOOLS
        for name in required_names(tool)
    ],
)
def test_omitting_a_required_property_is_rejected_by_name(
    tool_name: str,
    omitted: str,
    wired_store: None,
) -> None:
    """A `required` flag in tools.json has to be enforced by service.py, not just declared."""
    tool = next(candidate for candidate in TOOLS if candidate["name"] == tool_name)
    arguments = {
        prop["name"]: SAMPLE_VALUES[prop["type"]]
        for prop in tool["properties"]
        if prop["required"] and prop["name"] != omitted
    }

    event, context = create_invocation(tool_name, arguments)
    result = lambda_handler(event, context)

    assert f"'{omitted}'" in result.get("error", ""), result

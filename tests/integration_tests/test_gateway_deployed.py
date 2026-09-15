"""The deployed gateway and the Lambda behind it, exercised without the model.

Integration rather than end-to-end, and the distinction is the point: this
starts halfway along the path, at the gateway, and stops at the table. The
runtime, the model and the conversation memory are not in it, so a green run
here does not mean the agent answers - only that everything under it is wired
up. That claim needs a test starting where scripts/chat.py does, and there is
not one yet.

What it covers is what no offline test can: the gateway's own configuration,
the tool schemas terraform expanded into it, the roles along the path, and
whether the Lambda and the table exist at all. test_mcp.py checks the same MCP
framing against a substituted urlopen, and test_tool_contract.py drives the
same handler on moto. Nothing here calls Bedrock, so the assertions are exact
and a run costs nothing worth measuring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from todo_agent.models import USER_ID_KEY


if TYPE_CHECKING:
    from collections.abc import Callable


pytestmark = pytest.mark.aws

TOOLS_CONTRACT = Path(__file__).resolve().parents[2] / "infrastructure" / "tools.json"


def test_the_gateway_publishes_the_whole_tool_contract(tool_names: dict[str, str]) -> None:
    declared = {tool["name"] for tool in json.loads(TOOLS_CONTRACT.read_text())}

    assert set(tool_names) == declared


def test_add_item_writes_a_row_the_table_can_be_read_for(
    call: Callable[..., dict[str, Any]],
    table: Any,
    user_id: str,
) -> None:
    result = call("add_item", user_id, text="buy a milk", priority=4)

    # Verify: through boto3, not through the tool that wrote it.
    assert result["created"] is True
    row = table.get_item(Key={"user_id": user_id, "item_id": result["item"]["item_id"]})["Item"]
    assert row["text"] == "buy a milk"
    assert row["status"] == "PENDING"
    assert int(row["priority"]) == 4
    # The user id is the partition key, never part of what a tool hands back.
    assert USER_ID_KEY not in result["item"]


def test_list_items_returns_what_was_added_oldest_first(
    call: Callable[..., dict[str, Any]],
    user_id: str,
) -> None:
    call("add_item", user_id, text="buy a milk")
    call("add_item", user_id, text="call the bank")

    listed = call("list_items", user_id)

    assert listed["count"] == 2
    assert listed["truncated"] is False
    assert [item["text"] for item in listed["items"]] == ["buy a milk", "call the bank"]


def test_search_then_delete_removes_the_row(
    call: Callable[..., dict[str, Any]],
    table: Any,
    user_id: str,
) -> None:
    # Setup: the two-step the model has to perform, since it is never told an id.
    created = call("add_item", user_id, text="buy a milk")["item"]

    found = call("search_items", user_id, query="milk")
    assert [item["item_id"] for item in found["items"]] == [created["item_id"]]

    # Verify: the tool reports the delete and the row is actually gone.
    assert call("delete_item", user_id, item_id=created["item_id"])["deleted"] is True
    assert "Item" not in table.get_item(Key={"user_id": user_id, "item_id": created["item_id"]})


def test_a_call_carrying_no_identity_is_refused(call: Callable[..., dict[str, Any]]) -> None:
    """The refusal is the Lambda failing, not a tool returning an error result.

    service.py lets `MissingIdentityError` propagate on purpose: a missing
    identity is a plumbing failure, and folding it into an ordinary error result
    would hide it from the function's error metric. The gateway turns a failed
    Lambda into a generic message of its own, so what arrives here is "An
    internal error occurred. Please retry later." and not anything naming
    `user_id` — which is also the right amount to tell a caller that supplied
    none. Asserting on that wording would be asserting on AWS's, so what is
    checked is that the call was refused and created nothing.
    """
    result = call("add_item", None, text="should never reach the table")

    assert "error" in result
    assert "created" not in result


def test_one_users_items_are_invisible_to_another(
    call: Callable[..., dict[str, Any]],
    make_user: Callable[[], str],
) -> None:
    owner, stranger = make_user(), make_user()
    call("add_item", owner, text="private to the owner")

    assert call("list_items", stranger)["items"] == []
    assert call("search_items", stranger, query="private")["items"] == []

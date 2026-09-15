"""One conversation, from where the operator starts to what lands in DynamoDB.

This is the layer nothing else can stand in for. `local_agent.py` runs the same
loop with the gateway and the memory replaced, and the deployed gateway tests
run the tools with no model above them; between the two sits everything these
tests are about — the runtime process AgentCore launches, the environment it was
given, the model, the session, and the memory that makes a second turn mean
anything.

Every deployment failure this project has had lived exactly here: an entrypoint
the platform would not launch, a system prompt with a newline in it, a missing
boto3, a client with no region. Each one was found by a person typing into
scripts/chat.py after an apply, and each one is what this file is for.

The model is not deterministic, so nothing here asserts on its prose. What is
asserted is what it did: which tools it chose, in what order, and what is in the
table afterwards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from boto3.dynamodb.conditions import Key


if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.conftest import Turn


pytestmark = pytest.mark.aws


def assert_spoke_plainly(turn: Turn) -> None:
    """The two habits the system prompt forbids and the model has been caught in.

    Nova Pro writes `<thinking>` into ordinary text, which the runtime filters
    out; and it likes to quote the item_id it just used back at the user, which
    only the prompt can discourage. Both are regressions worth catching here,
    because neither is visible without a real model.
    """
    assert turn.text.strip(), "the agent said nothing"
    assert "<thinking>" not in turn.text
    assert "</thinking>" not in turn.text
    assert "item_id" not in turn.text


def test_a_turn_adds_the_item_the_user_asked_for(
    conversation: Callable[[str], Turn],
    table: Any,
    user_id: str,
) -> None:
    turn = conversation("add a new item to the list, buy a milk")

    # Verify: the stream ran to the end, the model reached for the right tool,
    # and the row exists in the real table.
    assert turn.errors == []
    assert turn.completed
    assert "add_item" in turn.tools, turn.tools

    rows = table.query(KeyConditionExpression=Key("user_id").eq(user_id))["Items"]
    assert len(rows) == 1
    assert "milk" in rows[0]["text"].lower()

    assert_spoke_plainly(turn)
    assert rows[0]["item_id"] not in turn.text


def test_deleting_by_wording_searches_for_the_id_first(
    conversation: Callable[[str], Turn],
    call: Callable[..., dict[str, Any]],
    table: Any,
    user_id: str,
) -> None:
    """The model is never told an id, so a delete has to be two tool calls."""
    # Setup through the gateway rather than through a turn: the second step is
    # what is under test, and a deterministic first one costs nothing.
    created = call("add_item", user_id, text="buy a milk")["item"]

    turn = conversation("delete buy a milk")

    assert turn.errors == []
    assert {"search_items", "delete_item"} <= set(turn.tools), turn.tools
    assert turn.tools.index("search_items") < turn.tools.index("delete_item")
    assert "Item" not in table.get_item(Key={"user_id": user_id, "item_id": created["item_id"]})
    assert_spoke_plainly(turn)


def test_a_second_turn_acts_on_what_the_first_created(
    conversation: Callable[[str], Turn],
    table: Any,
    user_id: str,
) -> None:
    """Two turns, one session id — the shape a conversation actually has.

    What this proves is the session lifecycle end to end: the runtime holds a
    session, the second turn resolves "that one" against it, and the update
    reaches the table. It is not proof that memory was *necessary* — with a
    single item on the list the model could also have found it by listing.
    Recall in isolation is test_memory.py's question.

    The second turn asks for a status change rather than a priority one because
    update_item has no priority field: priority can be set by add_item and never
    changed afterwards. Asked to change it anyway, Nova emits a malformed tool
    call and the whole stream dies with modelStreamErrorException — so this
    wording would have been testing that gap, not the session.
    """
    conversation("add a new item to the list, buy a milk")

    turn = conversation("mark that one as done")

    assert turn.errors == []
    assert "update_item" in turn.tools, turn.tools
    rows = table.query(KeyConditionExpression=Key("user_id").eq(user_id))["Items"]
    assert len(rows) == 1
    assert rows[0]["status"] == "DONE"
    assert_spoke_plainly(turn)

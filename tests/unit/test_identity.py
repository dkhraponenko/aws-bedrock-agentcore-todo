"""One user's list must be unreachable from another's session.

`user_id` is the partition key, so isolation is a property of the key rather
than of a check that could be forgotten. These tests hold the two halves of that
claim together: the runtime injects the identity, and the handler reads it from
the same place.
"""

from __future__ import annotations

import pytest

from tests.conftest import create_invocation
from todo_agent.errors import MissingIdentityError
from todo_agent.lambda_handler import lambda_handler
from todo_agent.models import USER_ID_KEY, ToolInvocation
from todo_runtime.agent import USER_ID_ARGUMENT


def add_item(user_id: str | None, text: str) -> dict[str, object]:
    """Add one item as a given user, the way the runtime would."""
    event, context = create_invocation("add_item", {"text": text}, user_id=user_id)
    return lambda_handler(event, context)


def list_items(user_id: str | None) -> list[str]:
    """The texts one user can see."""
    event, context = create_invocation("list_items", user_id=user_id)
    return [item["text"] for item in lambda_handler(event, context)["items"]]


def test_the_runtime_and_the_handler_agree_on_the_key() -> None:
    """Two packages, deployed separately, sharing one string by contract."""
    assert USER_ID_ARGUMENT == USER_ID_KEY


def test_items_are_invisible_to_another_user(wired_store: None) -> None:
    add_item("alice", "alice's milk")
    add_item("bob", "bob's bread")

    assert list_items("alice") == ["alice's milk"]
    assert list_items("bob") == ["bob's bread"]


@pytest.mark.parametrize("user_id", [None, "", "   "], ids=["absent", "empty", "blank"])
def test_an_invocation_without_an_identity_is_rejected(user_id: str | None, wired_store: None) -> None:
    """A default here would merge every caller into one shared partition.

    The identity is injected by the runtime, outside the model's reach, so its
    absence is a plumbing failure rather than something a retry could fix — it
    fails the invocation instead of picking a stand-in.
    """
    event, context = create_invocation("add_item", {"text": "x"}, user_id=user_id)

    with pytest.raises(MissingIdentityError, match=USER_ID_KEY):
        lambda_handler(event, context)


def test_the_identity_never_reaches_a_handler_as_an_argument() -> None:
    """It names the caller; a tool that saw it might treat it as data."""
    event, context = create_invocation("add_item", {"text": "x"}, user_id="alice")

    invocation = ToolInvocation.from_invocation(event, context)

    assert invocation.user_id == "alice"
    assert invocation.arguments == {"text": "x"}

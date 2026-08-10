"""Integration tests for TodoStore against a moto-backed DynamoDB table."""

from __future__ import annotations

from typing import TYPE_CHECKING

import boto3
import pytest
from moto import mock_aws

from tests.conftest import TABLE_NAME
from todo_agent.errors import ItemNotFoundError
from todo_agent.models import TodoItem, TodoStatus
from todo_agent.store import TodoStore


if TYPE_CHECKING:
    from collections.abc import Callable, Generator


USER = "demo-user"
OTHER_USER = "someone-else"


@pytest.fixture
def store(aws_credentials: None) -> Generator[TodoStore]:
    """A store backed by a real table schema, matching infrastructure/dynamodb.tf."""
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
        yield TodoStore(table_name=TABLE_NAME, dynamodb_client=client)


def test_add_then_get_round_trips_every_field(store: TodoStore) -> None:
    created = store.add(USER, "buy a milk", priority=1)

    fetched = store.get(USER, created.item_id)

    assert fetched == created
    assert fetched.status is TodoStatus.PENDING
    assert fetched.priority == 1


def test_list_returns_items_in_creation_order(store: TodoStore) -> None:
    first = store.add(USER, "first", priority=3)
    second = store.add(USER, "second", priority=3)

    items = store.list_all(USER)

    assert [item.item_id for item in items] == [first.item_id, second.item_id]


def test_list_is_scoped_to_one_user(store: TodoStore) -> None:
    store.add(USER, "mine", priority=3)
    store.add(OTHER_USER, "theirs", priority=3)

    assert [item.text for item in store.list_all(USER)] == ["mine"]
    assert [item.text for item in store.list_all(OTHER_USER)] == ["theirs"]


def test_list_filters_by_status_server_side(store: TodoStore) -> None:
    pending = store.add(USER, "still open", priority=3)
    done = store.add(USER, "finished", priority=3)
    store.update(USER, done.item_id, status=TodoStatus.DONE)

    assert [item.item_id for item in store.list_all(USER, status=TodoStatus.PENDING)] == [pending.item_id]
    assert [item.item_id for item in store.list_all(USER, status=TodoStatus.DONE)] == [done.item_id]


@pytest.mark.parametrize(
    "query",
    [
        pytest.param("milk", id="exact-word"),
        pytest.param("MILK", id="different-case"),
        pytest.param("a mil", id="partial-substring"),
    ],
)
def test_search_matches_case_insensitively(store: TodoStore, query: str) -> None:
    created = store.add(USER, "Buy a Milk", priority=3)

    assert [item.item_id for item in store.search(USER, query)] == [created.item_id]


def test_search_returns_nothing_when_no_text_matches(store: TodoStore) -> None:
    store.add(USER, "buy a milk", priority=3)

    assert store.search(USER, "mortgage") == []


def test_search_never_crosses_users(store: TodoStore) -> None:
    store.add(OTHER_USER, "buy a milk", priority=3)

    assert store.search(USER, "milk") == []


def test_update_changes_only_the_supplied_fields(store: TodoStore) -> None:
    created = store.add(USER, "buy a milk", priority=2)

    updated = store.update(USER, created.item_id, status=TodoStatus.DONE)

    assert updated.status is TodoStatus.DONE
    assert updated.text == "buy a milk"
    assert updated.priority == 2
    assert updated.created_at == created.created_at


def test_update_can_rewrite_text_and_status_together(store: TodoStore) -> None:
    created = store.add(USER, "buy a milk", priority=3)

    updated = store.update(USER, created.item_id, text="buy oat milk", status=TodoStatus.DONE)

    assert (updated.text, updated.status) == ("buy oat milk", TodoStatus.DONE)


def test_delete_removes_the_item_and_returns_it(store: TodoStore) -> None:
    created = store.add(USER, "buy a milk", priority=3)

    deleted = store.delete(USER, created.item_id)

    assert deleted.item_id == created.item_id
    assert store.list_all(USER) == []


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param("get", id="get"),
        pytest.param("update", id="update"),
        pytest.param("delete", id="delete"),
    ],
)
def test_missing_item_raises_item_not_found(store: TodoStore, operation: str) -> None:
    calls: dict[str, Callable[[], TodoItem]] = {
        "get": lambda: store.get(USER, "no-such-id"),
        "update": lambda: store.update(USER, "no-such-id", text="x"),
        "delete": lambda: store.delete(USER, "no-such-id"),
    }

    with pytest.raises(ItemNotFoundError, match="no-such-id"):
        calls[operation]()


def test_another_users_item_id_is_not_reachable(store: TodoStore) -> None:
    created = store.add(OTHER_USER, "theirs", priority=3)

    # Ownership is enforced by the key itself, not by a check after the read.
    with pytest.raises(ItemNotFoundError):
        store.delete(USER, created.item_id)

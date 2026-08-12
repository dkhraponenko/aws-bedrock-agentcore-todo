"""Store paths that a moto-backed test cannot reach: paging and non-domain errors."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from todo_agent.store import DynamoDBClient, TodoStore


USER = "demo-user"


def attribute_map(item_id: str, text: str) -> dict[str, Any]:
    return {
        "user_id": {"S": USER},
        "item_id": {"S": item_id},
        "text": {"S": text},
        "status": {"S": "PENDING"},
        "priority": {"N": "3"},
        "created_at": {"S": "2026-08-09T00:00:00+00:00"},
        "updated_at": {"S": "2026-08-09T00:00:00+00:00"},
    }


def test_query_follows_the_pagination_cursor() -> None:
    client = MagicMock(spec=DynamoDBClient)
    client.query.side_effect = [
        {"Items": [attribute_map("a", "first")], "LastEvaluatedKey": {"item_id": {"S": "a"}}},
        {"Items": [attribute_map("b", "second")]},
    ]
    store = TodoStore(table_name="t", dynamodb_client=client)

    items = store.list_all(USER)

    assert [item.text for item in items] == ["first", "second"]
    assert client.query.call_count == 2
    assert client.query.call_args_list[1].kwargs["ExclusiveStartKey"] == {"item_id": {"S": "a"}}


def test_list_all_stops_once_the_limit_is_reached() -> None:
    """The cap has to bound the read, not just the returned slice."""
    client = MagicMock(spec=DynamoDBClient)
    client.query.side_effect = [
        {"Items": [attribute_map("a", "first")], "LastEvaluatedKey": {"item_id": {"S": "a"}}},
        {"Items": [attribute_map("b", "second")]},
    ]
    store = TodoStore(table_name="t", dynamodb_client=client)

    items = store.list_all(USER, limit=1)

    assert [item.text for item in items] == ["first"]
    assert client.query.call_count == 1, "the second page must never be fetched"


@pytest.mark.parametrize(
    ("method", "call"),
    [
        pytest.param("update_item", lambda store: store.update(USER, "x", text="y"), id="update"),
        pytest.param("delete_item", lambda store: store.delete(USER, "x"), id="delete"),
    ],
)
def test_client_errors_other_than_a_failed_condition_propagate(
    method: str,
    call: Any,
) -> None:
    """Only ConditionalCheckFailed means "missing"; throttling must not be swallowed."""
    client = MagicMock(spec=DynamoDBClient)
    getattr(client, method).side_effect = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow down"}},
        method,
    )
    store = TodoStore(table_name="t", dynamodb_client=client)

    with pytest.raises(ClientError, match="ProvisionedThroughputExceeded"):
        call(store)

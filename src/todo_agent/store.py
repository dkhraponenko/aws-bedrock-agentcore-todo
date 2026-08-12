"""DynamoDB persistence for todo items."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from itertools import islice
from typing import TYPE_CHECKING, Any, Protocol

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.config import Config
from botocore.exceptions import ClientError

from todo_agent.errors import ItemNotFoundError
from todo_agent.models import TodoItem, TodoStatus, new_item_id, utc_now


if TYPE_CHECKING:
    from collections.abc import Iterator


logger = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("TODO_TABLE_NAME", "")
_CONDITION_FAILED = "ConditionalCheckFailedException"

# Text matching happens here, not in DynamoDB, so a query matching nothing would
# otherwise read the whole partition. Stopping early is reported, not hidden.
SEARCH_SCAN_LIMIT = 500

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


def _to_attribute_map(values: dict[str, Any]) -> dict[str, Any]:
    return {key: _serializer.serialize(value) for key, value in values.items()}


def _from_attribute_map(attributes: dict[str, Any]) -> dict[str, Any]:
    return {key: _deserializer.deserialize(value) for key, value in attributes.items()}


class DynamoDBClient(Protocol):
    """The five DynamoDB calls this store makes, and the exact set iam.tf grants.

    Structural, so the real client, `MagicMock(spec=...)` and moto all fit.
    """

    def put_item(self, **kwargs: Any) -> dict[str, Any]: ...  # noqa: ANN401

    def get_item(self, **kwargs: Any) -> dict[str, Any]: ...  # noqa: ANN401

    def query(self, **kwargs: Any) -> dict[str, Any]: ...  # noqa: ANN401

    def update_item(self, **kwargs: Any) -> dict[str, Any]: ...  # noqa: ANN401

    def delete_item(self, **kwargs: Any) -> dict[str, Any]: ...  # noqa: ANN401


@dataclass(frozen=True)
class SearchResult:
    """What a bounded search found, and whether it looked at the whole list.

    `exhaustive` is false when the search stopped early, so the caller can say
    "there may be more".
    """

    items: list[TodoItem]
    exhaustive: bool


class TodoStore:
    """Single-table access keyed by `user_id` (partition) and `item_id` (sort).

    Every read is a Query; nothing here Scans.
    """

    def __init__(self, table_name: str = "", dynamodb_client: DynamoDBClient | None = None) -> None:
        self.table_name = table_name or TABLE_NAME
        self.client: DynamoDBClient = dynamodb_client or boto3.client(
            "dynamodb",
            config=Config(
                connect_timeout=5,
                read_timeout=10,
                retries={"max_attempts": 2},
            ),
        )

    def add(self, user_id: str, text: str, priority: int) -> TodoItem:
        """Create a new item and return it."""
        now = utc_now()
        item = TodoItem(
            user_id=user_id,
            item_id=new_item_id(),
            text=text,
            status=TodoStatus.PENDING,
            priority=priority,
            created_at=now,
            updated_at=now,
        )
        self.client.put_item(
            TableName=self.table_name,
            Item=_to_attribute_map(item.to_dynamodb()),
            ConditionExpression="attribute_not_exists(item_id)",
        )
        logger.info("Created item", extra={"item_id": item.item_id, "user_id": user_id})
        return item

    def list_all(
        self,
        user_id: str,
        status: TodoStatus | None = None,
        limit: int | None = None,
    ) -> list[TodoItem]:
        """Return a user's items, oldest first, optionally by status.

        `limit` bounds the read, not just the slice returned: `_query` is a
        generator, so paging stops as soon as enough items have been seen.
        """
        items = self._query(user_id, status)
        return list(items if limit is None else islice(items, limit))

    def search(
        self,
        user_id: str,
        query: str,
        status: TodoStatus | None = None,
        limit: int | None = None,
    ) -> SearchResult:
        """Return items whose text contains `query`, case-insensitively.

        Status is pushed down as a FilterExpression; the text match stays here
        because DynamoDB's `contains()` is case-sensitive and "milk" should find
        "Buy Milk". That is what makes the read unbounded unless capped, so it
        stops at `limit` matches or `SEARCH_SCAN_LIMIT` items examined, and says
        which. A search never crosses a partition.
        """
        needle = query.casefold()
        matches: list[TodoItem] = []
        scanned = 0

        for item in islice(self._query(user_id, status), SEARCH_SCAN_LIMIT):
            scanned += 1
            if needle not in item.text.casefold():
                continue
            matches.append(item)
            if limit is not None and len(matches) >= limit:
                return SearchResult(items=matches, exhaustive=False)

        return SearchResult(items=matches, exhaustive=scanned < SEARCH_SCAN_LIMIT)

    def get(self, user_id: str, item_id: str) -> TodoItem:
        """Return a single item, or raise `ItemNotFoundError`."""
        response = self.client.get_item(
            TableName=self.table_name,
            Key=_to_attribute_map({"user_id": user_id, "item_id": item_id}),
        )
        raw = response.get("Item")
        if not raw:
            raise ItemNotFoundError(item_id)
        return TodoItem.from_dynamodb(_from_attribute_map(raw))

    def update(
        self,
        user_id: str,
        item_id: str,
        text: str | None = None,
        status: TodoStatus | None = None,
    ) -> TodoItem:
        """Apply a partial update and return the resulting item."""
        assignments = ["updated_at = :updated_at"]
        values: dict[str, Any] = {":updated_at": utc_now()}
        names: dict[str, str] = {}

        if text is not None:
            assignments.append("#text = :text")
            names["#text"] = "text"
            values[":text"] = text
        if status is not None:
            assignments.append("#status = :status")
            names["#status"] = "status"
            values[":status"] = str(status)

        update_kwargs: dict[str, Any] = {
            "TableName": self.table_name,
            "Key": _to_attribute_map({"user_id": user_id, "item_id": item_id}),
            "UpdateExpression": "SET " + ", ".join(assignments),
            "ExpressionAttributeValues": _to_attribute_map(values),
            "ConditionExpression": "attribute_exists(item_id)",
            "ReturnValues": "ALL_NEW",
        }
        if names:
            update_kwargs["ExpressionAttributeNames"] = names

        try:
            response = self.client.update_item(**update_kwargs)
        except ClientError as e:
            self._raise_if_missing(e, item_id)
            raise
        return TodoItem.from_dynamodb(_from_attribute_map(response["Attributes"]))

    def delete(self, user_id: str, item_id: str) -> TodoItem:
        """Delete an item and return what was deleted."""
        try:
            response = self.client.delete_item(
                TableName=self.table_name,
                Key=_to_attribute_map({"user_id": user_id, "item_id": item_id}),
                ConditionExpression="attribute_exists(item_id)",
                ReturnValues="ALL_OLD",
            )
        except ClientError as e:
            self._raise_if_missing(e, item_id)
            raise
        return TodoItem.from_dynamodb(_from_attribute_map(response["Attributes"]))

    def _query(self, user_id: str, status: TodoStatus | None) -> Iterator[TodoItem]:
        """Page through one user's partition, applying an optional status filter."""
        query_kwargs: dict[str, Any] = {
            "TableName": self.table_name,
            "KeyConditionExpression": "user_id = :user_id",
            "ExpressionAttributeValues": {":user_id": _serializer.serialize(user_id)},
        }
        if status is not None:
            query_kwargs["FilterExpression"] = "#status = :status"
            query_kwargs["ExpressionAttributeNames"] = {"#status": "status"}
            query_kwargs["ExpressionAttributeValues"][":status"] = _serializer.serialize(str(status))

        while True:
            response = self.client.query(**query_kwargs)
            for raw in response.get("Items", []):
                yield TodoItem.from_dynamodb(_from_attribute_map(raw))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return
            query_kwargs["ExclusiveStartKey"] = last_key

    @staticmethod
    def _raise_if_missing(error: ClientError, item_id: str) -> None:
        """Translate a failed condition check into a domain error."""
        code = error.response.get("Error", {}).get("Code", "Unknown")
        if code == _CONDITION_FAILED:
            raise ItemNotFoundError(item_id) from error

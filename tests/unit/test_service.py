"""Unit tests for gateway tool dispatch, validation and result shaping."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from tests.conftest import create_invocation
from todo_agent.errors import ItemNotFoundError
from todo_agent.lambda_handler import lambda_handler
from todo_agent.models import DEFAULT_PRIORITY, USER_ID_KEY, TodoItem, TodoStatus
from todo_agent.service import LIST_RESULT_LIMIT, SEARCH_RESULT_LIMIT, TOOL_NAMES, TodoService
from todo_agent.store import SearchResult, TodoStore


if TYPE_CHECKING:
    from collections.abc import Callable


def make_item(item_id: str = "abc123", text: str = "buy a milk", **overrides: Any) -> TodoItem:
    """Build a TodoItem with sensible defaults for assertions."""
    defaults: dict[str, Any] = {
        "user_id": "demo-user",
        "item_id": item_id,
        "text": text,
        "status": TodoStatus.PENDING,
        "priority": DEFAULT_PRIORITY,
        "created_at": "2026-08-08T10:00:00+00:00",
        "updated_at": "2026-08-08T10:00:00+00:00",
    }
    return TodoItem(**{**defaults, **overrides})


@pytest.fixture
def mock_store() -> MagicMock:
    """A type-safe stand-in for the DynamoDB-backed store."""
    return MagicMock(spec=TodoStore)


@pytest.fixture
def service(mock_store: MagicMock) -> type[TodoService]:
    """The service wired to a mock store."""
    TodoService.setup(store=mock_store)
    return TodoService


class TestAddItem:
    def test_add_item_success_returns_created_item(self, service: type[TodoService], mock_store: MagicMock) -> None:
        mock_store.add.return_value = make_item()

        result = service.process(*create_invocation("add_item", {"text": "buy a milk"}))

        mock_store.add.assert_called_once_with(user_id="demo-user", text="buy a milk", priority=DEFAULT_PRIORITY)
        assert result["created"] is True
        assert result["item"]["text"] == "buy a milk"
        assert "error" not in result

    def test_add_item_accepts_an_integer_priority(self, service: type[TodoService], mock_store: MagicMock) -> None:
        # The gateway preserves the JSON type declared in the tool schema.
        mock_store.add.return_value = make_item(priority=1)

        service.process(*create_invocation("add_item", {"text": "x", "priority": 1}))

        assert mock_store.add.call_args.kwargs["priority"] == 1

    def test_add_item_without_text_reports_an_error_and_skips_the_store(
        self, service: type[TodoService], mock_store: MagicMock
    ) -> None:
        result = service.process(*create_invocation("add_item", {}))

        mock_store.add.assert_not_called()
        assert "text" in result["error"]

    @pytest.mark.parametrize(
        "priority",
        [
            pytest.param(0, id="below-range"),
            pytest.param(6, id="above-range"),
            pytest.param("urgent", id="not-a-number"),
        ],
    )
    def test_add_item_rejects_invalid_priority(
        self, service: type[TodoService], mock_store: MagicMock, priority: object
    ) -> None:
        result = service.process(*create_invocation("add_item", {"text": "x", "priority": priority}))

        mock_store.add.assert_not_called()
        assert "priority" in result["error"]


class TestSearchAndList:
    def test_search_items_returns_ids_the_model_can_act_on(
        self, service: type[TodoService], mock_store: MagicMock
    ) -> None:
        mock_store.search.return_value = SearchResult(items=[make_item(item_id="id-1")], exhaustive=True)

        result = service.process(*create_invocation("search_items", {"query": "milk"}))

        mock_store.search.assert_called_once_with(
            user_id="demo-user", query="milk", status=None, limit=SEARCH_RESULT_LIMIT + 1
        )
        assert result["count"] == 1
        assert result["truncated"] is False
        assert result["items"][0]["item_id"] == "id-1"

    @pytest.mark.parametrize(
        ("matches", "expected_count"),
        [
            pytest.param(SEARCH_RESULT_LIMIT + 1, SEARCH_RESULT_LIMIT, id="more-matches-than-the-limit"),
            pytest.param(1, 1, id="scan-stopped-early"),
        ],
    )
    def test_search_items_flags_an_incomplete_answer(
        self,
        service: type[TodoService],
        mock_store: MagicMock,
        matches: int,
        expected_count: int,
    ) -> None:
        """Hitting the result cap and stopping the scan early say the same thing."""
        items = [make_item(item_id=f"id-{i}") for i in range(matches)]
        mock_store.search.return_value = SearchResult(items=items, exhaustive=False)

        result = service.process(*create_invocation("search_items", {"query": "milk"}))

        assert result["count"] == len(result["items"]) == expected_count
        assert result["truncated"] is True

    def test_list_items_passes_status_filter_through(self, service: type[TodoService], mock_store: MagicMock) -> None:
        mock_store.list_all.return_value = []

        service.process(*create_invocation("list_items", {"status": "done"}))

        mock_store.list_all.assert_called_once_with("demo-user", status=TodoStatus.DONE, limit=LIST_RESULT_LIMIT + 1)

    @pytest.mark.parametrize(
        ("stored", "expected_count", "expected_truncated"),
        [
            pytest.param(LIST_RESULT_LIMIT + 1, LIST_RESULT_LIMIT, True, id="over-the-limit"),
            pytest.param(1, 1, False, id="within-the-limit"),
        ],
    )
    def test_list_items_truncates_only_past_the_limit(
        self,
        service: type[TodoService],
        mock_store: MagicMock,
        stored: int,
        expected_count: int,
        expected_truncated: bool,
    ) -> None:
        """An unbounded list would be read into the Lambda and resent to the model whole."""
        mock_store.list_all.return_value = [make_item(item_id=f"id-{i}") for i in range(stored)]

        result = service.process(*create_invocation("list_items"))

        assert result["count"] == len(result["items"]) == expected_count
        assert result["truncated"] is expected_truncated

    def test_list_items_rejects_unknown_status(self, service: type[TodoService], mock_store: MagicMock) -> None:
        result = service.process(*create_invocation("list_items", {"status": "later"}))

        mock_store.list_all.assert_not_called()
        assert "status" in result["error"]


class TestUpdateAndDelete:
    def test_update_item_applies_partial_change(self, service: type[TodoService], mock_store: MagicMock) -> None:
        mock_store.update.return_value = make_item(status=TodoStatus.DONE)

        result = service.process(*create_invocation("update_item", {"item_id": "abc123", "status": "DONE"}))

        mock_store.update.assert_called_once_with(
            user_id="demo-user", item_id="abc123", text=None, status=TodoStatus.DONE
        )
        assert result["updated"] is True

    def test_update_item_without_any_change_reports_an_error(
        self, service: type[TodoService], mock_store: MagicMock
    ) -> None:
        result = service.process(*create_invocation("update_item", {"item_id": "abc123"}))

        mock_store.update.assert_not_called()
        assert "error" in result

    def test_delete_item_returns_what_was_deleted(self, service: type[TodoService], mock_store: MagicMock) -> None:
        mock_store.delete.return_value = make_item()

        result = service.process(*create_invocation("delete_item", {"item_id": "abc123"}))

        mock_store.delete.assert_called_once_with(user_id="demo-user", item_id="abc123")
        assert result["deleted"] is True

    def test_missing_item_is_reported_so_the_model_can_search_again(
        self, service: type[TodoService], mock_store: MagicMock
    ) -> None:
        mock_store.delete.side_effect = ItemNotFoundError("gone")

        result = service.process(*create_invocation("delete_item", {"item_id": "gone"}))

        assert "gone" in result["error"]


def test_search_items_runs_end_to_end_against_a_real_store(wired_store: None) -> None:
    """A mocked store cannot catch the handler mis-reading what the real one returns."""
    lambda_handler(*create_invocation("add_item", {"text": "buy a milk"}))

    result = lambda_handler(*create_invocation("search_items", {"query": "MILK"}))

    assert result["count"] == 1
    assert result["truncated"] is False
    assert result["items"][0]["text"] == "buy a milk"


class TestInvocationParsing:
    @pytest.mark.parametrize("target", ["some-other-target", ""], ids=["prefixed", "bare"])
    def test_the_tool_name_dispatches_with_or_without_a_target(
        self, service: type[TodoService], mock_store: MagicMock, target: str
    ) -> None:
        # The gateway sends "<target>___<tool>"; handlers key off the bare name.
        mock_store.list_all.return_value = []

        result = service.process(*create_invocation("list_items", target=target))

        assert result == {"count": 0, "truncated": False, "items": []}

    def test_missing_client_context_is_reported_not_raised(self, service: type[TodoService]) -> None:
        """A context without a tool name names no tool — which is an error result, not a crash."""
        result = service.process({USER_ID_KEY: "demo-user"}, object())

        assert "error" in result

    def test_unknown_tool_lists_what_is_available(self, service: type[TodoService]) -> None:
        result = service.process(*create_invocation("drop_table"))

        assert "drop_table" in result["error"]
        assert set(result["available_tools"]) == TOOL_NAMES

    def test_unexpected_store_error_is_reported_as_an_error_result(
        self, service: type[TodoService], mock_store: MagicMock
    ) -> None:
        mock_store.list_all.side_effect = RuntimeError("dynamodb exploded")

        result = service.process(*create_invocation("list_items"))

        assert "dynamodb exploded" in result["error"]

    def test_process_before_setup_raises(self) -> None:
        with pytest.raises(RuntimeError, match="not initialized"):
            TodoService.process(*create_invocation("list_items"))

    def test_every_tool_in_the_schema_is_dispatchable(
        self,
        service: type[TodoService],
        invocation_factory: Callable[..., tuple[dict[str, Any], Any]],
    ) -> None:
        # Guards against a Terraform inline_payload with no matching handler.
        declared = {"add_item", "list_items", "search_items", "update_item", "delete_item"}
        assert declared == TOOL_NAMES

        for tool in sorted(declared):
            result = service.process(*invocation_factory(tool))
            assert "available_tools" not in result, tool

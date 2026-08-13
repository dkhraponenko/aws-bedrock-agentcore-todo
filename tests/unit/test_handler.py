"""Unit tests for the Lambda entry point."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

from tests.conftest import create_invocation
from todo_agent import lambda_handler as handler_module
from todo_agent.service import TodoService
from todo_agent.store import TodoStore


def test_lambda_handler_delegates_to_the_service() -> None:
    # Both halves of the invocation have to arrive: the arguments are the event,
    # and the tool name only exists on the context, so a handler that dropped
    # either would fail to dispatch at all.
    store = MagicMock(spec=TodoStore)
    store.list_all.return_value = []
    TodoService.setup(store=store)

    event, context = create_invocation("list_items")

    assert handler_module.lambda_handler(event, context) == {"count": 0, "truncated": False, "items": []}


def test_importing_the_module_wires_the_store_once() -> None:
    # setup() runs at import time so the DynamoDB client is built once per
    # container instead of once per invocation.
    TodoService._store = None

    importlib.reload(handler_module)

    assert isinstance(TodoService._store, TodoStore)

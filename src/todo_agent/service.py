"""AgentCore Gateway tool service: parse the invocation, dispatch, return a result."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from todo_agent.errors import TodoError, UnknownFunctionError, ValidationError
from todo_agent.models import (
    DEFAULT_PRIORITY,
    MAX_PRIORITY,
    MIN_PRIORITY,
    TodoStatus,
    ToolInvocation,
)
from todo_agent.store import TodoStore


if TYPE_CHECKING:
    from collections.abc import Callable


logger = logging.getLogger(__name__)

SEARCH_RESULT_LIMIT = 25

# Capped far higher than a search, but still capped: an uncapped partition is
# read into the Lambda and resent to the model every turn.
LIST_RESULT_LIMIT = 100


def _require_str(invocation: ToolInvocation, name: str) -> str:
    """Return a required string argument, or raise `ValidationError`."""
    value = str(invocation.arguments.get(name) or "").strip()
    if not value:
        msg = f"Parameter '{name}' is required and must not be empty."
        raise ValidationError(msg)
    return value


def _optional_str(invocation: ToolInvocation, name: str) -> str | None:
    """Return an optional string argument, or None when absent."""
    value = str(invocation.arguments.get(name) or "").strip()
    return value or None


def _optional_priority(invocation: ToolInvocation) -> int:
    """Return the priority argument, defaulting when omitted. Accepts int or numeric string."""
    raw = invocation.arguments.get("priority")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return DEFAULT_PRIORITY
    try:
        priority = int(raw)
    except (TypeError, ValueError) as e:
        msg = f"Parameter 'priority' must be an integer between {MIN_PRIORITY} and {MAX_PRIORITY}."
        raise ValidationError(msg) from e
    if not MIN_PRIORITY <= priority <= MAX_PRIORITY:
        msg = f"Parameter 'priority' must be between {MIN_PRIORITY} and {MAX_PRIORITY}, got {priority}."
        raise ValidationError(msg)
    return priority


def _optional_status(invocation: ToolInvocation) -> TodoStatus | None:
    """Return the status argument as an enum, or None when absent."""
    raw = _optional_str(invocation, "status")
    if raw is None:
        return None
    try:
        return TodoStatus(raw.upper())
    except ValueError as e:
        allowed = ", ".join(str(status) for status in TodoStatus)
        msg = f"Parameter 'status' must be one of: {allowed}."
        raise ValidationError(msg) from e


def add_item(store: TodoStore, invocation: ToolInvocation) -> dict[str, Any]:
    """Create a new todo item for the current user."""
    item = store.add(
        user_id=invocation.user_id,
        text=_require_str(invocation, "text"),
        priority=_optional_priority(invocation),
    )
    return {"created": True, "item": item.to_agent()}


def list_items(store: TodoStore, invocation: ToolInvocation) -> dict[str, Any]:
    """Return the current user's list, oldest first, up to `LIST_RESULT_LIMIT`."""
    # One past the cap, so "there are more" needs no second query.
    items = store.list_all(
        invocation.user_id,
        status=_optional_status(invocation),
        limit=LIST_RESULT_LIMIT + 1,
    )
    truncated = items[:LIST_RESULT_LIMIT]
    return {
        "count": len(truncated),
        "truncated": len(items) > len(truncated),
        "items": [item.to_agent() for item in truncated],
    }


def search_items(store: TodoStore, invocation: ToolInvocation) -> dict[str, Any]:
    """Return items whose text matches the query — the model's only source of `item_id`."""
    matches = store.search(
        user_id=invocation.user_id,
        query=_require_str(invocation, "query"),
        status=_optional_status(invocation),
    )
    truncated = matches[:SEARCH_RESULT_LIMIT]
    return {
        "count": len(truncated),
        "truncated": len(matches) > len(truncated),
        "items": [item.to_agent() for item in truncated],
    }


def update_item(store: TodoStore, invocation: ToolInvocation) -> dict[str, Any]:
    """Update the text and/or status of one item by id."""
    # Required parameters first: a missing item_id should say so, rather than
    # the emptier "nothing to update" below.
    item_id = _require_str(invocation, "item_id")

    text = _optional_str(invocation, "text")
    status = _optional_status(invocation)
    if text is None and status is None:
        msg = "Provide at least one of 'text' or 'status' to update."
        raise ValidationError(msg)

    item = store.update(
        user_id=invocation.user_id,
        item_id=item_id,
        text=text,
        status=status,
    )
    return {"updated": True, "item": item.to_agent()}


def delete_item(store: TodoStore, invocation: ToolInvocation) -> dict[str, Any]:
    """Delete one item by id."""
    item = store.delete(
        user_id=invocation.user_id,
        item_id=_require_str(invocation, "item_id"),
    )
    return {"deleted": True, "item": item.to_agent()}


_HANDLERS: dict[str, Callable[[TodoStore, ToolInvocation], dict[str, Any]]] = {
    "add_item": add_item,
    "list_items": list_items,
    "search_items": search_items,
    "update_item": update_item,
    "delete_item": delete_item,
}

TOOL_NAMES = frozenset(_HANDLERS)


class TodoService:
    """Dispatches one gateway tool call, holding the store across invocations.

    The return value reaches the model unchanged and the contract has no status
    flag, so failures come back as an ordinary result with an `error` key.
    """

    _store: TodoStore | None = None

    @classmethod
    def setup(cls, store: TodoStore | None = None) -> None:
        """Initialise the shared store. Called at module import in the handler."""
        cls._store = store or TodoStore()

    @classmethod
    def process(cls, event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ANN401
        """Handle one gateway tool invocation.

        Raises:
            RuntimeError: `setup()` was never called.
            MissingIdentityError: The invocation carried no caller identity.
                Not caught: an error result would report a plumbing failure as
                an ordinary tool outcome instead of raising the error metric.
        """
        if cls._store is None:
            msg = "TodoService not initialized. Call setup() first."
            raise RuntimeError(msg)

        invocation = ToolInvocation.from_invocation(event, context)
        logger.info(
            "Handling gateway tool call",
            extra={"tool": invocation.tool_name, "user_id": invocation.user_id},
        )

        handler = _HANDLERS.get(invocation.tool_name)
        if handler is None:
            unknown = UnknownFunctionError(invocation.tool_name)
            logger.warning("Unsupported tool", extra={"tool": invocation.tool_name})
            return {"error": str(unknown), "available_tools": sorted(TOOL_NAMES)}

        try:
            return handler(cls._store, invocation)
        except TodoError as e:
            # Recoverable: the message names the problem, the model retries.
            logger.warning("Rejected tool call", extra={"tool": invocation.tool_name, "error": str(e)})
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Unhandled error in tool", extra={"tool": invocation.tool_name})
            return {"error": f"Internal error while handling '{invocation.tool_name}': {e}"}

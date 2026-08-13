"""The JSON log formatter: `extra=` has to survive into what CloudWatch stores."""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING, Any

import pytest

from todo_logging.json_logs import JsonFormatter, configure


if TYPE_CHECKING:
    from collections.abc import Callable, Generator


@pytest.fixture(autouse=True)
def restore_root() -> Generator[None]:
    """configure() mutates the root logger; keep that out of other tests."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers, root.level = handlers, level


@pytest.fixture
def emit() -> Callable[..., dict[str, Any]]:
    """Log one record through the formatter and return the decoded JSON."""

    def _emit(message: str = "Created item", exc_info: bool = False, **extra: Any) -> dict[str, Any]:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())

        logger = logging.getLogger("todo_agent.store")
        logger.handlers, logger.propagate, logger.level = [handler], False, logging.INFO
        logger.info(message, extra=extra or None, exc_info=exc_info)
        return dict(json.loads(stream.getvalue()))

    return _emit


def test_extra_fields_reach_the_output_and_record_internals_do_not(
    emit: Callable[..., dict[str, Any]],
) -> None:
    """The whole point: ten call sites pass ids this way, and nothing else rides along."""
    entry = emit(item_id="a1", user_id="alice")

    assert entry["item_id"] == "a1"
    assert entry["user_id"] == "alice"
    assert not {"msg", "args", "levelno", "pathname", "created"} & set(entry)


def test_level_logger_and_message_are_always_present(emit: Callable[..., dict[str, Any]]) -> None:
    entry = emit()

    assert entry["level"] == "INFO"
    assert entry["logger"] == "todo_agent.store"
    assert entry["message"] == "Created item"
    assert entry["timestamp"].endswith("+00:00")


def _raise_boom() -> None:
    msg = "boom"
    raise ValueError(msg)


def test_an_exception_is_rendered_as_text(emit: Callable[..., dict[str, Any]]) -> None:
    try:
        _raise_boom()
    except ValueError:
        entry = emit("Unhandled error in tool", exc_info=True, tool="add_item")

    assert "ValueError: boom" in entry["exception"]
    assert entry["tool"] == "add_item"


def test_a_value_that_is_not_json_serialisable_falls_back_to_repr(
    emit: Callable[..., dict[str, Any]],
) -> None:
    """A log line must never be the thing that raises."""
    entry = emit(error=ValueError("boom"))

    assert "boom" in entry["error"]


@pytest.mark.parametrize("installed", [1, 0], ids=["platform-installed-one", "platform-installed-none"])
def test_configure_leaves_exactly_one_json_handler(installed: int) -> None:
    """Lambda installs its own handler; adding a second one would log everything twice."""
    root = logging.getLogger()
    root.handlers = [logging.StreamHandler(io.StringIO()) for _ in range(installed)]

    configure("WARNING")

    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    assert root.level == logging.WARNING

"""Domain errors raised by the todo tools."""

from __future__ import annotations


class TodoError(Exception):
    """Base class for errors the agent is allowed to see."""


class ValidationError(TodoError):
    """Input from the agent was missing or malformed.

    Recoverable: the message names what was wrong so the model can call the
    tool again with corrected arguments.
    """


class ItemNotFoundError(TodoError):
    """No item with the requested id exists for this user."""

    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        super().__init__(f"No item found with item_id '{item_id}'")


class UnknownFunctionError(TodoError):
    """The agent invoked a tool this Lambda does not implement."""

    def __init__(self, function_name: str) -> None:
        self.function_name = function_name
        super().__init__(f"Unknown function '{function_name}'")

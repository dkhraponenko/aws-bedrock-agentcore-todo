"""Shared fixtures and invocation factories for the todo tool tests."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from todo_agent.models import TOOL_NAME_KEY, TOOL_NAME_SEPARATOR
from todo_agent.service import TodoService


if TYPE_CHECKING:
    from collections.abc import Callable, Generator


TARGET_NAME = "todo"
TABLE_NAME = os.environ.get("TODO_TABLE_NAME", "todo-agent-items-test")


@dataclass
class FakeClientContext:
    """Stands in for the `client_context` AWS attaches to a Lambda context."""

    custom: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeLambdaContext:
    """Minimal LambdaContext double carrying only what the handler reads."""

    client_context: FakeClientContext | None = None


def create_invocation(
    tool: str,
    arguments: dict[str, Any] | None = None,
    target: str = TARGET_NAME,
) -> tuple[dict[str, Any], FakeLambdaContext]:
    """Build the (event, context) pair AgentCore Gateway would deliver.

    The arguments are the whole event; the tool name rides in the client
    context, prefixed with the gateway target name.
    """
    qualified = f"{target}{TOOL_NAME_SEPARATOR}{tool}" if target else tool
    context = FakeLambdaContext(client_context=FakeClientContext(custom={TOOL_NAME_KEY: qualified}))
    return dict(arguments or {}), context


@pytest.fixture
def invocation_factory() -> Callable[..., tuple[dict[str, Any], FakeLambdaContext]]:
    """Expose the invocation factory as a fixture for readability in tests."""
    return create_invocation


@pytest.fixture(autouse=True)
def reset_service() -> Generator[None]:
    """Keep the module-level service singleton from leaking between tests."""
    TodoService._store = None
    yield
    TodoService._store = None


@pytest.fixture
def aws_credentials() -> Generator[None]:
    """Point botocore at fake credentials so moto never touches real AWS."""
    originals = {
        key: os.environ.get(key)
        for key in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SECURITY_TOKEN",
            "AWS_SESSION_TOKEN",
            "AWS_DEFAULT_REGION",
        )
    }
    os.environ.update(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",  # noqa: S106 — deliberately fake, so moto never reaches real AWS
        AWS_SECURITY_TOKEN="testing",  # noqa: S106
        AWS_SESSION_TOKEN="testing",  # noqa: S106
        AWS_DEFAULT_REGION="us-east-1",
    )
    yield
    for key, value in originals.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

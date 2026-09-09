"""The tool path from a gateway invocation down to a real store.

A mocked store answers whatever the test told it to, which is what makes
test_service.py fast and also what makes it blind: it cannot catch the handler
mis-reading a shape the real store actually returns. These tests run the whole
path — lambda_handler, the service, the store, moto's DynamoDB — and assert on
what comes back out.
"""

from __future__ import annotations

from tests.conftest import create_invocation
from todo_agent.lambda_handler import lambda_handler


def test_search_finds_an_item_the_same_path_created(wired_store: None) -> None:
    lambda_handler(*create_invocation("add_item", {"text": "buy a milk"}))

    result = lambda_handler(*create_invocation("search_items", {"query": "MILK"}))

    assert result["count"] == 1
    assert result["truncated"] is False
    assert result["items"][0]["text"] == "buy a milk"

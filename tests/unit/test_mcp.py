"""The MCP client: JSON-RPC framing, SigV4 signing and result unwrapping."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import Message
from typing import TYPE_CHECKING, Any, cast

import pytest
from botocore.credentials import Credentials

from todo_runtime.mcp import GatewayClient, GatewayError, decode_tool_result


if TYPE_CHECKING:
    from collections.abc import Callable

    Transport = Callable[..., "FakeTransport"]


GATEWAY_URL = "https://gateway.invalid/mcp"


class FakeResponse:
    """Stands in for the object urlopen returns."""

    def __init__(self, body: str, content_type: str = "application/json", session_id: str | None = None) -> None:
        self._body = body.encode()
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if session_id is not None:
            self.headers["Mcp-Session-Id"] = session_id

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return


class FakeTransport:
    """Answers each request with the next prepared body, recording what was sent."""

    def __init__(self, *responses: FakeResponse) -> None:
        self._responses = list(responses)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, **_: object) -> FakeResponse:
        self.requests.append(request)
        return self._responses.pop(0)

    @property
    def methods(self) -> list[str]:
        """The JSON-RPC method of every request sent, in order."""
        return [sent_body(request).get("method", "") for request in self.requests]


def sent_body(request: urllib.request.Request) -> dict[str, Any]:
    """The JSON-RPC payload of one recorded request."""
    return dict(json.loads(cast("bytes", request.data)))


def rpc(result: dict[str, Any], request_id: int = 1) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})


@pytest.fixture
def client() -> GatewayClient:
    return GatewayClient(GATEWAY_URL, "us-east-1", Credentials("access", "secret", "token"))


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Transport:
    """Install a transport in place of urlopen and hand it back for assertions."""

    def install(*responses: FakeResponse) -> FakeTransport:
        fake = FakeTransport(*responses)
        monkeypatch.setattr(urllib.request, "urlopen", fake)
        return fake

    return install


def test_url_must_be_https() -> None:
    with pytest.raises(ValueError, match="must be https"):
        GatewayClient("http://gateway.invalid/mcp", "us-east-1", Credentials("a", "b"))


def test_handshake_precedes_the_first_call_and_is_not_repeated(client: GatewayClient, transport: Transport) -> None:
    sent = transport(
        FakeResponse(rpc({"protocolVersion": "2025-06-18"}), session_id="session-9"),
        FakeResponse("", content_type="text/plain"),
        FakeResponse(rpc({"tools": [{"name": "todo___add_item"}]})),
        FakeResponse(rpc({"tools": []})),
    )

    client.list_tools()
    client.list_tools()

    assert sent.methods == ["initialize", "notifications/initialized", "tools/list", "tools/list"]


def test_requests_are_signed_and_echo_the_session_id(client: GatewayClient, transport: Transport) -> None:
    sent = transport(
        FakeResponse(rpc({}), session_id="session-9"),
        FakeResponse("", content_type="text/plain"),
        FakeResponse(rpc({"tools": []})),
    )

    client.list_tools()

    headers = sent.requests[-1].headers
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256")
    assert headers["X-amz-security-token"] == "token"
    assert headers["Mcp-session-id"] == "session-9"


def test_tool_call_sends_name_and_arguments(client: GatewayClient, transport: Transport) -> None:
    sent = transport(
        FakeResponse(rpc({}), session_id="s"),
        FakeResponse("", content_type="text/plain"),
        FakeResponse(rpc({"content": [{"type": "text", "text": '{"created": true}'}]})),
    )

    result = client.call_tool("todo___add_item", {"text": "buy a milk", "user_id": "alice"})

    assert result == {"created": True}
    assert sent_body(sent.requests[-1])["params"] == {
        "name": "todo___add_item",
        "arguments": {"text": "buy a milk", "user_id": "alice"},
    }


def test_event_stream_framing_is_unwrapped(client: GatewayClient, transport: Transport) -> None:
    body = f"event: message\ndata: {rpc({'tools': [{'name': 'todo___list_items'}]})}\n\n"
    transport(
        FakeResponse(rpc({}), session_id="s"),
        FakeResponse("", content_type="text/plain"),
        FakeResponse(body, content_type="text/event-stream"),
    )

    assert client.list_tools() == [{"name": "todo___list_items"}]


def test_event_stream_without_data_frames_is_an_error(client: GatewayClient, transport: Transport) -> None:
    transport(FakeResponse("event: ping\n\n", content_type="text/event-stream"))

    with pytest.raises(GatewayError, match="no data frames"):
        client.list_tools()


def test_jsonrpc_error_becomes_a_gateway_error(client: GatewayClient, transport: Transport) -> None:
    transport(FakeResponse(json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": "nope"}})))

    with pytest.raises(GatewayError, match="Gateway rejected initialize: nope"):
        client.list_tools()


def test_http_error_becomes_a_gateway_error(client: GatewayClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_403(*_: object, **__: object) -> None:
        raise urllib.error.HTTPError(GATEWAY_URL, 403, "Forbidden", Message(), io.BytesIO(b"AccessDenied"))

    monkeypatch.setattr(urllib.request, "urlopen", raise_403)

    with pytest.raises(GatewayError, match=r"HTTP 403.*AccessDenied"):
        client.list_tools()


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        pytest.param(
            {"content": [{"type": "text", "text": '{"count": 0}'}]},
            {"count": 0},
            id="json-object",
        ),
        pytest.param(
            {"content": [{"type": "text", "text": "boom"}], "isError": True},
            {"error": "boom"},
            id="tool-error",
        ),
        pytest.param(
            {"content": [], "isError": True},
            {"error": "The gateway reported a tool error."},
            id="error-without-text",
        ),
        pytest.param(
            {"content": [{"type": "text", "text": "not json"}]},
            {"error": "Tool returned text that is not JSON: not json"},
            id="unparseable",
        ),
        pytest.param(
            {"content": [{"type": "text", "text": "[1, 2]"}]},
            {"result": [1, 2]},
            id="json-but-not-an-object",
        ),
    ],
)
def test_decode_tool_result(result: dict[str, Any], expected: dict[str, Any]) -> None:
    assert decode_tool_result(result) == expected

"""MCP client for the AgentCore Gateway.

No boto3 operation invokes a gateway: it is a plain MCP endpoint authorised with
SigV4, so the JSON-RPC framing and the signing both happen here.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


if TYPE_CHECKING:
    from botocore.credentials import Credentials


logger = logging.getLogger(__name__)

SIGNING_SERVICE = "bedrock-agentcore"
PROTOCOL_VERSION = "2025-06-18"
SESSION_HEADER = "Mcp-Session-Id"
JSON_CONTENT_TYPE = "application/json"
SSE_CONTENT_TYPE = "text/event-stream"
CLIENT_INFO = {"name": "todo-runtime", "version": "1"}

# Error bodies reach logs and model-visible messages, so they are truncated.
ERROR_PREVIEW_CHARS = 200


class GatewayError(RuntimeError):
    """The gateway returned a JSON-RPC error or a response that cannot be used."""


def _parse_message(content_type: str, body: str) -> dict[str, Any]:
    """Return the JSON-RPC message, unwrapping SSE framing when present.

    Raises:
        GatewayError: The body carried no usable message.
    """
    if content_type != SSE_CONTENT_TYPE:
        return dict(json.loads(body))

    # Streamable HTTP is allowed to answer a single request with an event
    # stream; the last data frame is the response to it.
    frames = [line.partition(":")[2].strip() for line in body.splitlines() if line.startswith("data:")]
    if not frames:
        msg = "Gateway returned an event stream with no data frames."
        raise GatewayError(msg)
    return dict(json.loads(frames[-1]))


def decode_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the MCP content block the gateway wraps a tool's return value in.

    Failures come back as an ordinary result with an `error` key, matching the
    tool itself, so the model handles both the same way.
    """
    text = "".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")

    if result.get("isError"):
        return {"error": text or "The gateway reported a tool error."}

    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return {"error": f"Tool returned text that is not JSON: {text[:ERROR_PREVIEW_CHARS]}"}

    # Anything but an object is still worth handing to the model.
    return decoded if isinstance(decoded, dict) else {"result": decoded}


class GatewayClient:
    """Speaks MCP to one AgentCore Gateway.

    Stateful: `initialize` returns a session id every later request echoes, so
    the handshake runs once per client.
    """

    def __init__(
        self,
        url: str,
        region: str,
        credentials: Credentials,
        timeout: float = 30.0,
    ) -> None:
        # So a misconfigured variable cannot point urlopen at file://.
        if urllib.parse.urlparse(url).scheme != "https":
            msg = f"Gateway URL must be https, got {url!r}."
            raise ValueError(msg)

        self._url = url
        self._region = region
        self._credentials = credentials
        self._timeout = timeout
        self._session_id: str | None = None
        self._connected = False
        self._request_id = 0

    def _send(self, payload: dict[str, Any]) -> tuple[str, str, str | None]:
        """Sign one JSON-RPC payload, post it, and return the raw response."""
        body = json.dumps(payload).encode()
        headers = {
            "Content-Type": JSON_CONTENT_TYPE,
            "Accept": f"{JSON_CONTENT_TYPE}, {SSE_CONTENT_TYPE}",
        }
        if self._session_id is not None:
            headers[SESSION_HEADER] = self._session_id

        signed = AWSRequest(method="POST", url=self._url, data=body, headers=headers)
        SigV4Auth(self._credentials, SIGNING_SERVICE, self._region).add_auth(signed)

        request = urllib.request.Request(self._url, data=body, headers=dict(signed.headers), method="POST")  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                return (
                    response.headers.get_content_type(),
                    response.read().decode(),
                    response.headers.get(SESSION_HEADER),
                )
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            msg = f"Gateway returned HTTP {e.code}: {detail[:ERROR_PREVIEW_CHARS]}"
            raise GatewayError(msg) from e

    def _call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Issue one JSON-RPC request and return its result object."""
        self._request_id += 1
        content_type, body, session_id = self._send(
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params or {},
            }
        )
        if session_id:
            self._session_id = session_id

        message = _parse_message(content_type, body)
        if error := message.get("error"):
            msg = f"Gateway rejected {method}: {error.get('message', error)}"
            raise GatewayError(msg)
        return dict(message.get("result", {}))

    def _notify(self, method: str) -> None:
        """Send a JSON-RPC notification, which by definition has no reply."""
        self._send({"jsonrpc": "2.0", "method": method})

    def connect(self) -> None:
        """Run the MCP handshake. Safe to call more than once.

        Tracked with its own flag rather than by the presence of a session id:
        the session header is optional in the protocol, and keying off it would
        repeat the handshake on every call against a gateway that omits it.
        """
        if self._connected:
            return

        self._call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        self._notify("notifications/initialized")
        self._connected = True
        logger.info("MCP session established", extra={"session_id": self._session_id})

    def list_tools(self) -> list[dict[str, Any]]:
        """Return the tool schemas the gateway publishes.

        Fetched rather than read from `tools.json`, so the model is offered
        exactly what the gateway will accept and the runtime artifact does not
        have to ship a copy of the contract.
        """
        self.connect()
        return list(self._call("tools/list").get("tools", []))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one tool and return its decoded result."""
        self.connect()
        return decode_tool_result(self._call("tools/call", {"name": name, "arguments": arguments}))

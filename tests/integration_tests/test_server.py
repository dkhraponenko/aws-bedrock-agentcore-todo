"""The transport: the two paths AgentCore polls and posts to, and their framing."""

from __future__ import annotations

import json
import socket
import threading
from http.client import HTTPConnection, HTTPResponse
from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import pytest

from todo_runtime import server


if TYPE_CHECKING:
    from collections.abc import Generator, Iterator


TIMEOUT = 5


class Recorder:
    """Stands in for one turn: records the payload, yields prepared frames."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.frames: list[str] = []
        self.started = threading.Event()
        self.release: threading.Event | None = None

    def __call__(self, payload: dict[str, Any]) -> Iterator[str]:
        self.payloads.append(payload)
        self.started.set()
        if self.release is not None:
            self.release.wait(TIMEOUT)
        yield from self.frames


@pytest.fixture
def turn() -> Recorder:
    return Recorder()


@pytest.fixture
def port(turn: Recorder) -> Generator[int]:
    """A real server on a real socket: the framing is most of what is tested."""

    class Handler(server.AgentHandler):
        stream = staticmethod(turn)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()
    thread.join(TIMEOUT)


def call(port: int, method: str, path: str, body: dict[str, Any] | str | None = None) -> HTTPResponse:
    """Make one request and read the whole response."""
    connection = HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
    payload = body if isinstance(body, str) else json.dumps(body) if body is not None else None
    connection.request(method, path, body=payload)
    response = connection.getresponse()
    response.read()
    connection.close()
    return response


def frames(raw: bytes) -> list[dict[str, Any]]:
    """Decode a server-sent event body back into events."""
    return [json.loads(line.removeprefix("data:")) for line in raw.decode().split("\n\n") if line.strip()]


def test_ping_reports_healthy(port: int) -> None:
    connection = HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
    connection.request("GET", "/ping")
    response = connection.getresponse()
    body = json.loads(response.read())
    connection.close()

    assert response.status == 200
    assert response.getheader("Content-Type") == "application/json"
    assert body == {"status": "Healthy"}
    # A timestamp that moved on every ping would keep the session alive forever.
    assert "time_of_last_update" not in body


def test_a_turn_is_streamed_as_it_is_produced(port: int, turn: Recorder) -> None:
    turn.frames = ['data: {"type": "text", "text": "Hi."}\n\n', 'data: {"type": "done"}\n\n']

    connection = HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
    connection.request("POST", "/invocations", body=json.dumps({"prompt": "hi", "user_id": "alice"}))
    response = connection.getresponse()
    raw = response.read()
    connection.close()

    assert response.status == 200
    assert response.getheader("Content-Type") == "text/event-stream"
    assert response.getheader("Transfer-Encoding") == "chunked"
    assert frames(raw) == [{"type": "text", "text": "Hi."}, {"type": "done"}]
    assert turn.payloads == [{"prompt": "hi", "user_id": "alice"}]


def test_ping_answers_while_a_turn_is_still_running(port: int, turn: Recorder) -> None:
    """The health check decides whether the instance lives; a turn must not block it."""
    turn.release = threading.Event()
    turn.frames = ['data: {"type": "done"}\n\n']

    streaming = HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
    streaming.request("POST", "/invocations", body=json.dumps({"prompt": "hi", "user_id": "alice"}))
    assert turn.started.wait(TIMEOUT)

    try:
        assert call(port, "GET", "/ping").status == 200
    finally:
        turn.release.set()
        streaming.getresponse().read()
        streaming.close()


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/"), ("GET", "/invocations"), ("POST", "/ping"), ("POST", "/anything")],
)
def test_only_the_two_documented_paths_are_served(port: int, method: str, path: str) -> None:
    assert call(port, method, path, body={} if method == "POST" else None).status == 404


@pytest.mark.parametrize("body", ["not json", '"a string"', "[1, 2]"], ids=["invalid", "string", "list"])
def test_a_body_that_is_not_a_json_object_is_refused(port: int, body: str, turn: Recorder) -> None:
    assert call(port, "POST", "/invocations", body=body).status == 400
    assert turn.payloads == []


def test_an_empty_body_is_still_a_turn(port: int, turn: Recorder) -> None:
    """The turn itself answers a missing prompt; the transport does not second-guess it."""
    turn.frames = ['data: {"type": "done"}\n\n']

    assert call(port, "POST", "/invocations").status == 200
    assert turn.payloads == [{}]


def test_an_oversized_payload_is_refused_before_it_is_read(port: int, turn: Recorder) -> None:
    """The Content-Length alone decides, so the body never reaches memory."""
    with socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT) as sock:
        request = b"POST /invocations HTTP/1.1\r\nHost: localhost\r\nContent-Length: %d\r\n\r\n" % (
            server.MAX_PAYLOAD_BYTES + 1
        )
        sock.sendall(request)
        status = sock.recv(64).split(b" ")[1]

    assert status == b"413"
    assert turn.payloads == []


def test_the_access_log_goes_through_logging(port: int, caplog: pytest.LogCaptureFixture) -> None:
    """Anything written straight to stderr arrives in CloudWatch unformatted."""
    with caplog.at_level("INFO", logger="todo_runtime.server"):
        call(port, "GET", "/ping")

    assert any("/ping" in record.getMessage() for record in caplog.records)


def test_serve_binds_the_documented_address() -> None:
    built: list[tuple[Any, ...]] = []

    class FakeServer:
        def __init__(self, address: tuple[str, int], handler: type) -> None:
            built.append((address, handler))

        def serve_forever(self) -> None:
            built.append(("served",))

    server.serve(port=1234, server_class=FakeServer)  # type: ignore[arg-type]

    assert built == [((server.HOST, 1234), server.AgentHandler), ("served",)]

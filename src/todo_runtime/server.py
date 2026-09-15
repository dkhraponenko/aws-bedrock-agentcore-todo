"""The HTTP contract AgentCore Runtime speaks, and the process that serves it.

The service does not import this package and call a function: it unpacks the
zip, runs the entry point as a process, and talks HTTP to it on port 8080.
`POST /invocations` carries the turn, `GET /ping` decides whether the instance
is replaced. A turn itself lives in `todo_runtime.entrypoint`.

Threaded because `/ping` is polled while a turn is still streaming, and a
single-threaded server would answer it only after the model finished.
"""

from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, ClassVar, override

from todo_runtime.entrypoint import AgentService


if TYPE_CHECKING:
    from collections.abc import Iterator


logger = logging.getLogger(__name__)

# The runtime reaches this process from outside the container, so binding to
# localhost would make every health check fail.
HOST = "0.0.0.0"  # noqa: S104
PORT = 8080

# Named because two places have to agree on it: the route, and the access log
# that deliberately says nothing about it.
PING_PATH = "/ping"

# A turn is one prompt, so anything this size is a mistake or an attack — and
# reading it before deciding that is what to avoid.
MAX_PAYLOAD_BYTES = 256 * 1024


class AgentHandler(BaseHTTPRequestHandler):
    """One request: a health check, or one turn streamed back as it happens."""

    protocol_version = "HTTP/1.1"

    # The only seam a test needs, on the class because the server constructs a
    # handler per request.
    stream: ClassVar[staticmethod[[dict[str, Any]], Iterator[str]]] = staticmethod(AgentService.stream)

    def do_GET(self) -> None:
        """Answer the health check; anything else is not a path this serves."""
        if self.path.split("?")[0] != PING_PATH:
            self._respond(HTTPStatus.NOT_FOUND, {"message": "Not found."})
            return

        # No time_of_last_update: a timestamp moving on every ping reads as a
        # status that keeps changing, and the idle timeout never fires.
        self._respond(HTTPStatus.OK, {"status": "Healthy"})

    def do_POST(self) -> None:
        """Run one turn and stream its events back as server-sent events."""
        if self.path.split("?")[0] != "/invocations":
            self._respond(HTTPStatus.NOT_FOUND, {"message": "Not found."})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_PAYLOAD_BYTES:
            self._respond(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"message": "Payload too large."})
            return

        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._respond(HTTPStatus.BAD_REQUEST, {"message": "Body is not JSON."})
            return

        if not isinstance(payload, dict):
            self._respond(HTTPStatus.BAD_REQUEST, {"message": "Body is not a JSON object."})
            return

        self._stream(payload)

    @override
    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Log every request except a health check that passed.

        AgentCore polls /ping twice a second: some 170,000 lines a day, in a
        group billed by the gigabyte. A probe that did not return 200 is still
        logged, since that is what someone would come looking for.
        """
        if self.path.split("?")[0] == PING_PATH and code == HTTPStatus.OK:
            return
        super().log_request(code, size)

    @override
    def log_message(self, format: str, *args: Any) -> None:
        """Send the access log through `logging` instead of straight to stderr.

        The default would be the one line reaching CloudWatch unformatted.
        """
        logger.info("%s", format % args)

    def _respond(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        """Send one complete JSON response."""
        rendered = json.dumps(body).encode()

        # An unread body left in the socket is read as the next request line,
        # turning one refusal into a stream of parse errors.
        self.close_connection = status >= HTTPStatus.BAD_REQUEST

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(rendered)))
        self.end_headers()
        self.wfile.write(rendered)

    def _stream(self, payload: dict[str, Any]) -> None:
        """Write the turn's frames as they are produced, not once it is over.

        Chunked rather than a closed connection: the length is unknown when the
        headers go out, and a finished stream has to differ from a dropped one.
        """
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        for frame in type(self).stream(payload):
            encoded = frame.encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(encoded), encoded))
            self.wfile.flush()

        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def serve(port: int = PORT, server_class: type[ThreadingHTTPServer] = ThreadingHTTPServer) -> None:
    """Serve turns until the process is stopped.

    The contract fixes the port at 8080; `server_class` is a test seam.
    """
    server = server_class((HOST, port), AgentHandler)
    logger.info("Agent runtime listening", extra={"port": port})
    server.serve_forever()

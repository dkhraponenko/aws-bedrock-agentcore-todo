"""JSON log formatting shared by both deployables.

Call sites pass context as `extra=`, which the standard library attaches to the
record and then never prints. Owning the formatter is what makes `extra` reach
CloudWatch, and both runtimes format a line the same way.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any


# Everything `logging` puts on a record itself. Deriving the set from a real
# record keeps it correct across versions, where a hand-written list would
# quietly start leaking a newly added attribute into every log line.
_RECORD_ATTRIBUTES = frozenset(
    logging.LogRecord(name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None).__dict__
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Renders a record as one JSON object, `extra=` keys included."""

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as a single-line JSON document."""
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # What is left is the call site's `extra`, plus anything the platform's
        # filter added, such as Lambda's aws_request_id.
        entry.update({key: value for key, value in record.__dict__.items() if key not in _RECORD_ATTRIBUTES})

        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        # A log line must never be the thing that raises, so an argument that
        # does not serialise is rendered rather than propagated.
        return json.dumps(entry, ensure_ascii=False, default=repr)


def configure(level: str) -> None:
    """Set the root level and put the JSON formatter on the handler in use.

    Reuses whatever handler the platform installed instead of adding one:
    Lambda attaches its own, and a second handler would emit every line twice.

    Args:
        level: Root log level name, e.g. the value of `LOG_LEVEL`.
    """
    root = logging.getLogger()
    root.setLevel(level)

    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        handler.setFormatter(JsonFormatter())

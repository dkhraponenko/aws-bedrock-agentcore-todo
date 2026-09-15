#!/usr/bin/env python3
"""Interactive smoke test: talk to the deployed agent and watch its tool calls.

Configuration comes from .env, which scripts/sync_env.sh writes from the
terraform outputs; an exported variable still wins over the file:

    USER_ID=bob python scripts/chat.py

The dimmed lines show which tool the model picked, and two runs with different
USER_ID values show the isolation.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv


if TYPE_CHECKING:
    from collections.abc import Iterator


DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

RESULT_PREVIEW_CHARS = 300
SSE_PREFIX = b"data:"

# The repository root, one level up from scripts/, is where sync_env.sh writes.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

# What a cached ARN looks like once the runtime behind it was replaced. The
# file is a snapshot, so the fix is to regenerate it.
STALE_ARN_CODES = ("ResourceNotFoundException", "ValidationException")


class AgentCoreRuntime(Protocol):
    """The single bedrock-agentcore call this script makes."""

    # kwargs mirror the botocore request shape and are genuinely dynamic.
    def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]: ...  # noqa: ANN401


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Missing required environment variable {name}. Run scripts/sync_env.sh to write .env.")
    return value


def _render(event: dict[str, Any]) -> None:
    """Print one event from the agent's stream."""
    kind = event.get("type")
    if kind == "text":
        sys.stdout.write(event["text"])
        sys.stdout.flush()
    elif kind == "tool_use":
        print(f"\n{DIM}  -> {event['name']}{RESET}")
    elif kind == "tool_result":
        rendered = json.dumps(event["result"], ensure_ascii=False)[:RESULT_PREVIEW_CHARS]
        print(f"{DIM}     {rendered}{RESET}")
    elif kind == "error":
        print(f"\n[error] {event.get('message', '')}")


def stream_turn(
    client: AgentCoreRuntime,
    runtime_arn: str,
    user_id: str,
    session_id: str,
    prompt: str,
) -> Iterator[dict[str, Any]]:
    """Send one turn and yield its events, decoded out of their SSE frames.

    Split out from the rendering below so tests/e2e drives the same invocation
    and the same frame parsing the operator does, not a second copy of it.
    `user_id` is who the turn acts as; `session_id` is what gives it memory.
    """
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        runtimeUserId=user_id,
        contentType="application/json",
        payload=json.dumps({"prompt": prompt, "user_id": user_id, "session_id": session_id}).encode(),
    )

    for line in response["response"].iter_lines():
        if line.startswith(SSE_PREFIX):
            yield json.loads(line[len(SSE_PREFIX) :].strip())


def converse(client: AgentCoreRuntime, runtime_arn: str, user_id: str, session_id: str, prompt: str) -> None:
    """Send one turn and render the agent's stream as it arrives."""
    for event in stream_turn(client, runtime_arn, user_id, session_id, prompt):
        _render(event)
    print()


def main() -> None:
    # override=False: the environment beats the file, so `USER_ID=bob` needs no
    # edit. A missing .env is not an error — the variables can be exported by
    # hand, and _require_env names whichever is absent.
    load_dotenv(ENV_FILE, override=False)

    runtime_arn = _require_env("AGENT_RUNTIME_ARN")
    region = os.environ.get("AWS_REGION", "us-east-1")
    user_id = os.environ.get("USER_ID", "cli-operator")

    client: AgentCoreRuntime = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(connect_timeout=5, read_timeout=120, retries={"max_attempts": 2}),
    )

    # One session id for the whole conversation keeps the agent's context.
    session_id = str(uuid.uuid4())
    print(f"{BOLD}Session {session_id}{RESET}  user {user_id}  (Ctrl-D to exit)")

    while True:
        try:
            prompt = input(f"\n{BOLD}you >{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not prompt:
            continue

        print(f"{BOLD}agent >{RESET} ", end="")
        try:
            converse(client, runtime_arn, user_id, session_id, prompt)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "Unknown")
            print(f"\n{code}: {e}")
            if code in STALE_ARN_CODES:
                print(f"{DIM}     .env may predate the last apply; rerun scripts/sync_env.sh{RESET}")


if __name__ == "__main__":
    main()

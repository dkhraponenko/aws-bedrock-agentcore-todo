#!/usr/bin/env python3
"""Interactive smoke test: talk to the deployed agent and watch its tool calls.

Reads AGENT_RUNTIME_ARN and AWS_REGION from the environment; `terraform output
chat_command` prints a ready-to-run invocation.

    AWS_REGION=us-east-1 AGENT_RUNTIME_ARN=arn:aws:bedrock-agentcore:... python scripts/chat.py

Type a request ("add a new item to the list, buy a milk"), and the dimmed lines
show which tool the model picked — that is the part worth watching, especially
on "delete buy a milk", where it has to call search_items before delete_item.

Set USER_ID to two different values across two runs to see the isolation: the
lists, and the conversation history, are per user.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

RESULT_PREVIEW_CHARS = 300
SSE_PREFIX = b"data:"


class AgentCoreRuntime(Protocol):
    """The single bedrock-agentcore call this script makes."""

    # kwargs mirror the botocore request shape and are genuinely dynamic.
    def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]: ...  # noqa: ANN401


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Missing required environment variable {name}. See `terraform output chat_command`.")
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


def converse(client: AgentCoreRuntime, runtime_arn: str, user_id: str, session_id: str, prompt: str) -> None:
    """Send one turn and render the agent's stream as it arrives."""
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        runtimeSessionId=session_id,
        runtimeUserId=user_id,
        contentType="application/json",
        payload=json.dumps({"prompt": prompt, "user_id": user_id, "session_id": session_id}).encode(),
    )

    for line in response["response"].iter_lines():
        if line.startswith(SSE_PREFIX):
            _render(json.loads(line[len(SSE_PREFIX) :].strip()))
    print()


def main() -> None:
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


if __name__ == "__main__":
    main()

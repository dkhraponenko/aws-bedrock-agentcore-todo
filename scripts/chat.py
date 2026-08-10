#!/usr/bin/env python3
"""Interactive smoke test: talk to the deployed harness and watch its tool calls.

Reads HARNESS_ARN and AWS_REGION from the environment; `terraform output
chat_command` prints a ready-to-run invocation.

    AWS_REGION=us-east-1 HARNESS_ARN=arn:aws:bedrock-agentcore:... python scripts/chat.py

Type a request ("add a new item to the list, buy a milk"), and the dimmed
lines show which tool the model picked — that is the part worth watching,
especially on "delete buy a milk", where it has to call search_items before
delete_item.
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
# Anything else means the turn ended for a reason worth showing the operator.
EXPECTED_STOP_REASONS = frozenset({"end_turn", "tool_use", "tool_result"})


class AgentCoreRuntime(Protocol):
    """The single bedrock-agentcore call this script makes."""

    # kwargs mirror the botocore request shape and are genuinely dynamic.
    def invoke_harness(self, **kwargs: Any) -> dict[str, Any]: ...  # noqa: ANN401


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Missing required environment variable {name}. See `terraform output chat_command`.")
    return value


def _render_delta(delta: dict[str, Any], answer: list[str]) -> None:
    """Print one content-block delta and collect assistant text."""
    if text := delta.get("text"):
        answer.append(text)
        sys.stdout.write(text)
        sys.stdout.flush()
    elif tool_input := delta.get("toolUse"):
        # Arguments stream in as a JSON fragment per delta.
        print(f"{DIM}{tool_input['input']}{RESET}", end="")
    elif results := delta.get("toolResult"):
        rendered = json.dumps(results, ensure_ascii=False)[:RESULT_PREVIEW_CHARS]
        print(f"\n{DIM}     {rendered}{RESET}")


def _render_event(event: dict[str, Any], answer: list[str]) -> None:
    """Print whatever one stream event carries."""
    if start := event.get("contentBlockStart"):
        if tool := start.get("start", {}).get("toolUse"):
            print(f"\n{DIM}  -> {tool['name']} {RESET}", end="")
    elif delta_event := event.get("contentBlockDelta"):
        _render_delta(delta_event["delta"], answer)
    elif stop := event.get("messageStop"):
        reason = stop["stopReason"]
        if reason not in EXPECTED_STOP_REASONS:
            print(f"\n{DIM}[stopped: {reason}]{RESET}")
    elif error := (
        event.get("validationException") or event.get("internalServerException") or event.get("runtimeClientError")
    ):
        print(f"\n[error] {error.get('message', error)}")


def converse(client: AgentCoreRuntime, harness_arn: str, session_id: str, prompt: str) -> str:
    """Send one turn and stream the response, printing tool activity as it arrives.

    Returns the assistant's final text so the caller can keep a transcript.
    """
    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
    )

    answer: list[str] = []
    for event in response["stream"]:
        _render_event(event, answer)

    print()
    return "".join(answer)


def main() -> None:
    harness_arn = _require_env("HARNESS_ARN")
    region = os.environ.get("AWS_REGION", "us-east-1")

    client: AgentCoreRuntime = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(connect_timeout=5, read_timeout=120, retries={"max_attempts": 2}),
    )

    # One session id for the whole conversation keeps the harness's context.
    session_id = str(uuid.uuid4())
    print(f"{BOLD}Session {session_id}{RESET}  (Ctrl-D to exit)")

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
            converse(client, harness_arn, session_id, prompt)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "Unknown")
            print(f"\n{code}: {e}")


if __name__ == "__main__":
    main()

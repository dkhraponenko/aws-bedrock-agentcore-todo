#!/usr/bin/env python3
"""Compose docs/architecture.svg with the official AWS Architecture Icons inlined.

The icons are not redistributed here: point ICON_PACK at an extracted copy of the
AWS Architecture Icons download and rerun this after changing the stack.

    ICON_PACK=path/to/extracted/pack python docs/build_diagram.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


# A placeholder rather than anyone's real download path: the pack extracts to a
# hash-suffixed directory name, so ICON_PACK is expected to be set explicitly and
# main() below says so when it is not.
DEFAULT_PACK = "~/Downloads/aws-architecture-icons"
ICON_PACK = Path(os.environ.get("ICON_PACK", DEFAULT_PACK)).expanduser()
SERVICE = ICON_PACK / "Architecture-Service-Icons_07312026"
OUT = Path(__file__).resolve().parent / "architecture.svg"

ICONS = {
    "agentcore": SERVICE / "Arch_Artificial-Intelligence/48/Arch_Amazon-Bedrock-AgentCore_48.svg",
    "lambda": SERVICE / "Arch_Compute/48/Arch_AWS-Lambda_48.svg",
    "dynamodb": SERVICE / "Arch_Databases/48/Arch_Amazon-DynamoDB_48.svg",
    "iam": SERVICE / "Arch_Security-Identity/48/Arch_AWS-Identity-and-Access-Management_48.svg",
    "cloudwatch": SERVICE / "Arch_Management-Tools/48/Arch_Amazon-CloudWatch_48.svg",
}

ROOT_SVG = re.compile(r"<svg\b[^>]*>(.*)</svg>\s*$", re.DOTALL)
XML_DECL = re.compile(r"<\?xml[^>]*\?>\s*")
TITLE = re.compile(r"<title>.*?</title>\s*", re.DOTALL)

TEAL, ORANGE, PURPLE, RED, PINK = "#01a88d", "#ed7100", "#c925d1", "#dd344c", "#e7157b"


def inner(path: Path) -> str:
    """Return one icon's markup without its root <svg> or <title>."""
    raw = XML_DECL.sub("", path.read_text(encoding="utf-8"))
    match = ROOT_SVG.search(raw)
    if match is None:
        msg = f"no root <svg> in {path}"
        raise ValueError(msg)
    return TITLE.sub("", match.group(1)).strip()


def namespaced(body: str, prefix: str) -> str:
    """Prefix every internal id so several inlined icons cannot collide."""
    for name in sorted(set(re.findall(r'id="([^"]+)"', body)), key=len, reverse=True):
        body = body.replace(f'id="{name}"', f'id="{prefix}-{name}"')
        body = body.replace(f"url(#{name})", f"url(#{prefix}-{name})")
        body = body.replace(f'href="#{name}"', f'href="#{prefix}-{name}"')
    return body


USES: dict[str, int] = {}


def icon(name: str, x: int, y: int, size: int) -> str:
    """Inline one icon, scaled from its 64px artboard into a square slot."""
    USES[name] = USES.get(name, 0) + 1
    return (
        f'<svg x="{x}" y="{y}" width="{size}" height="{size}" viewBox="0 0 64 64" '
        f'role="presentation">{namespaced(inner(ICONS[name]), f"{name}{USES[name]}")}</svg>'
    )


def block(x: int, y: int, w: int, h: int, stroke: str) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="11" fill="#ffffff" '
        f'stroke="{stroke}" stroke-width="1.8"/>'
    )


def text(x: int, y: int, content: str, size: str = "13", fill: str = "#5f6b7a", weight: str = "400") -> str:
    return f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}">{content}</text>'


def rail(x: int, y: int, stroke: str, name: str, title: str, subtitle: str) -> str:
    """One side annotation: an icon, a bold label and a line of detail."""
    return "\n    ".join([
        f'<rect x="{x}" y="{y}" width="230" height="66" rx="10" fill="#ffffff" stroke="{stroke}" stroke-width="1.5"/>',
        icon(name, x + 14, y + 14, 38),
        text(x + 62, y + 30, title, size="13", fill="#16191f", weight="600"),
        text(x + 62, y + 48, subtitle, size="11.5"),
    ])


def build() -> str:
    """Render the whole diagram."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 700" width="1000" height="700" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif">
  <title>todo-agent architecture: an AgentCore Runtime loop calling tools through a gateway</title>
  <!-- Service icons are the official AWS Architecture Icons, inlined unmodified. -->

  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#5f6b7a"/>
    </marker>
  </defs>

  <rect width="1000" height="700" fill="#ffffff"/>

  <rect x="24" y="118" width="952" height="546" rx="14" fill="#fafbfc" stroke="#c8ced6" stroke-width="1.5" stroke-dasharray="7 5"/>
  <text x="46" y="146" font-size="13" fill="#5f6b7a" letter-spacing="1.4">AWS ACCOUNT &#183; us-east-1</text>

  <!-- the caller: not an AWS service, so not an AWS icon -->
  <g>
    <rect x="310" y="26" width="380" height="72" rx="11" fill="#ffffff" stroke="#d5dbdb" stroke-width="1.5"/>
    <g transform="translate(326, 38)">
      <rect width="48" height="48" rx="9" fill="#232f3e"/>
      <rect x="10" y="13" width="28" height="22" rx="3" fill="none" stroke="#ffffff" stroke-width="2"/>
      <path d="M15 20 l5 4 -5 4" fill="none" stroke="#ffffff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M23 28 h7" stroke="#ffffff" stroke-width="2" stroke-linecap="round"/>
    </g>
    {text(390, 57, "scripts/chat.py", size="16", fill="#16191f", weight="600")}
    {text(390, 78, "&#8220;delete buy a milk&#8221; &#183; USER_ID")}
  </g>

  <path d="M500 98 V 158" stroke="#5f6b7a" stroke-width="2" marker-end="url(#arrow)"/>
  <text x="512" y="134" font-size="12.5" fill="#5f6b7a">InvokeAgentRuntime</text>

  <g>
    {block(310, 158, 380, 84, TEAL)}
    {icon("agentcore", 326, 176, 48)}
    {text(390, 194, "AgentCore Runtime", size="16", fill="#16191f", weight="600")}
    {text(390, 214, "src/todo_runtime &#183; zip from S3, no image")}
    {text(390, 232, "Converse loop &#183; identity injected per call")}
  </g>

  <path d="M500 242 V 302" stroke="#5f6b7a" stroke-width="2" marker-end="url(#arrow)"/>
  <text x="512" y="278" font-size="12.5" fill="#5f6b7a">MCP over HTTP &#183; SigV4</text>

  <g>
    {block(310, 302, 380, 84, TEAL)}
    {icon("agentcore", 326, 320, 48)}
    {text(390, 338, "AgentCore Gateway", size="16", fill="#16191f", weight="600")}
    {text(390, 358, "MCP facade &#183; inbound auth AWS_IAM")}
    {text(390, 376, "target &#8220;todo&#8221; &#183; 5 tool schemas")}
  </g>

  <path d="M500 386 V 446" stroke="#5f6b7a" stroke-width="2" marker-end="url(#arrow)"/>
  <text x="512" y="422" font-size="12.5" fill="#5f6b7a">lambda:InvokeFunction</text>

  <g>
    {block(310, 446, 380, 84, ORANGE)}
    {icon("lambda", 326, 464, 48)}
    {text(390, 482, "Lambda &#183; todo_agent", size="16", fill="#16191f", weight="600")}
    {text(390, 502, "event = tool arguments, flat JSON")}
    {text(390, 520, "tool name in client_context.custom")}
  </g>

  <path d="M500 530 V 590" stroke="#5f6b7a" stroke-width="2" marker-end="url(#arrow)"/>
  <text x="512" y="566" font-size="12.5" fill="#5f6b7a">Query / Put / Update / Delete</text>

  <g>
    {block(310, 590, 380, 60, PURPLE)}
    {icon("dynamodb", 326, 596, 48)}
    {text(390, 614, "DynamoDB", size="16", fill="#16191f", weight="600")}
    {text(390, 634, "PK user_id &#183; SK item_id &#183; no Scans")}
  </g>

  <g>
    {rail(40, 176, RED, "iam", "runtime role", "model, gateway, memory")}
  </g>
  <path d="M270 209 H 310" stroke="{RED}" stroke-width="1.5" stroke-dasharray="4 3"/>

  <g>
    {rail(40, 320, RED, "iam", "gateway role", "one action, one function")}
  </g>
  <path d="M270 353 H 310" stroke="{RED}" stroke-width="1.5" stroke-dasharray="4 3"/>

  <g>
    {rail(40, 464, RED, "iam", "lambda role", "5 actions, one table")}
  </g>
  <path d="M270 497 H 310" stroke="{RED}" stroke-width="1.5" stroke-dasharray="4 3"/>

  <g>
    {rail(730, 176, TEAL, "agentcore", "AgentCore Memory", "history, one actor per user")}
  </g>
  <path d="M690 209 H 730" stroke="{TEAL}" stroke-width="1.5" stroke-dasharray="4 3"/>

  <g>
    {rail(730, 464, PINK, "cloudwatch", "CloudWatch Logs", "14-day retention")}
  </g>
  <path d="M690 497 H 730" stroke="{PINK}" stroke-width="1.5" stroke-dasharray="4 3"/>
</svg>
"""


def main() -> int:
    if not SERVICE.is_dir():
        print(f"AWS icon pack not found at {ICON_PACK}; set ICON_PACK to an extracted copy.", file=sys.stderr)
        return 1

    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes), icons used: {USES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

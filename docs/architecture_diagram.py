"""The stack as one request travels through it, rendered by the aws-diagram skill.

Replaces a generator whose every coordinate was typed by hand and whose icons came
from an external pack the repository could not carry. Regenerate with:

    python3 ~/.claude/skills/aws-diagram/scripts/awsdiag.py render \
        docs/architecture_diagram.py -o docs/architecture.svg --png

Never edit docs/architecture.svg: the next render discards the edit.
"""

from awsdiag import Col, Diagram, Edge, Group, Node, Row


RED = "#dd344c"
PINK = "#e7157b"
SPINE = 380


def rail(node_id: str, icon: str, title: str, detail: str, target: str) -> Node:
    """A side annotation, lined up with the spine node it describes."""
    return Node(id=node_id, icon=icon, title=title, lines=[detail], compact=True, align_with=target)


DIAGRAM = Diagram(
    title="todo-agent",
    subtitle=(
        "One turn, from the terminal to the table. Request path only: CI, the S3 state "
        "bucket and the bootstrap root are separate concerns."
    ),
    root=Col(
        Node(
            id="caller",
            glyph="terminal",
            title="scripts/chat.py",
            lines=["“delete buy a milk” · USER_ID"],
            width=SPINE,
        ),
        Group(
            kind="account",
            label="AWS Account · us-east-1",
            child=Row(
                Col(
                    rail("runtime_role", "iam", "runtime role", "model, gateway, memory", "runtime"),
                    rail("gateway_role", "iam", "gateway role", "one action, one function", "gateway"),
                    rail("lambda_role", "iam", "lambda role", "5 actions, one table", "fn"),
                    gap=64,
                ),
                Col(
                    Node(
                        id="runtime",
                        icon="agentcore",
                        title="AgentCore Runtime",
                        lines=[
                            "python main.py · HTTP :8080 /invocations",
                            "Converse loop · identity injected per call",
                        ],
                        width=SPINE,
                    ),
                    Node(
                        id="gateway",
                        icon="agentcore",
                        title="AgentCore Gateway",
                        lines=["MCP facade · inbound auth AWS_IAM", "target “todo” · 5 tool schemas"],
                        width=SPINE,
                    ),
                    Node(
                        id="fn",
                        icon="lambda",
                        title="Lambda · todo_agent",
                        lines=["event = tool arguments, flat JSON", "tool name in client_context.custom"],
                        width=SPINE,
                    ),
                    Node(
                        id="table",
                        icon="dynamodb",
                        title="DynamoDB",
                        lines=["PK user_id · SK item_id · no Scans"],
                        width=SPINE,
                    ),
                    gap=64,
                ),
                Col(
                    rail("model", "bedrock", "Bedrock · Nova Pro", "reached by inference profile", "runtime"),
                    Node(
                        id="memory",
                        icon="agentcore",
                        title="AgentCore Memory",
                        lines=["CreateEvent / ListEvents", "one actor per user · 7-day expiry"],
                        compact=True,
                        align_with="gateway",
                    ),
                    Node(
                        id="logs",
                        icon="cloudwatch",
                        title="CloudWatch Logs",
                        lines=["Lambda group · 14-day retention", "runtime group · created by the service"],
                        compact=True,
                        align_with="fn",
                    ),
                    gap=64,
                ),
                gap=64,
                align="start",
            ),
        ),
        gap=60,
    ),
    edges=[
        Edge("caller", "runtime", "InvokeAgentRuntime"),
        Edge("runtime", "model", "Converse"),
        Edge("runtime", "gateway", "MCP over HTTP · SigV4"),
        Edge("gateway", "fn", "lambda:InvokeFunction"),
        Edge("fn", "table", "Query / Put / Update / Delete"),
        Edge("runtime", "memory"),
        Edge("runtime_role", "runtime", style="dashed", color=RED),
        Edge("gateway_role", "gateway", style="dashed", color=RED),
        Edge("lambda_role", "fn", style="dashed", color=RED),
        Edge("fn", "logs", style="dashed", color=PINK),
    ],
    legend=[
        ("solid", "synchronous call"),
        ("dashed", "grant or dependency"),
    ],
)

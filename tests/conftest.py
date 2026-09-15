"""Shared fixtures and invocation factories for the todo tool tests."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3
import pytest
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from dotenv import dotenv_values
from moto import mock_aws
from scripts.chat import stream_turn

from todo_agent.models import TOOL_NAME_KEY, TOOL_NAME_SEPARATOR, USER_ID_KEY
from todo_agent.service import TodoService
from todo_agent.store import TodoStore
from todo_runtime.mcp import GatewayClient


if TYPE_CHECKING:
    from collections.abc import Callable, Generator


TARGET_NAME = "todo"
TABLE_NAME = os.environ.get("TODO_TABLE_NAME", "todo-agent-items-test")

# The runtime injects the caller's identity into every tool call, and the
# handler rejects an invocation that arrives without one. Tests stand in for
# the runtime, so the identity is supplied here rather than defaulted in the
# production code — pass `user_id=None` to exercise its absence.
TEST_USER_ID = "demo-user"


@dataclass
class FakeClientContext:
    """Stands in for the `client_context` AWS attaches to a Lambda context."""

    custom: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeLambdaContext:
    """Minimal LambdaContext double carrying only what the handler reads."""

    client_context: FakeClientContext | None = None


def create_invocation(
    tool: str,
    arguments: dict[str, Any] | None = None,
    target: str = TARGET_NAME,
    user_id: str | None = TEST_USER_ID,
) -> tuple[dict[str, Any], FakeLambdaContext]:
    """Build the (event, context) pair AgentCore Gateway would deliver.

    The arguments are the whole event; the tool name rides in the client
    context, prefixed with the gateway target name. `user_id` is merged in the
    way the runtime merges it — last, over anything the model supplied.
    """
    qualified = f"{target}{TOOL_NAME_SEPARATOR}{tool}" if target else tool
    context = FakeLambdaContext(client_context=FakeClientContext(custom={TOOL_NAME_KEY: qualified}))

    event = dict(arguments or {})
    if user_id is not None:
        event[USER_ID_KEY] = user_id
    return event, context


@pytest.fixture
def invocation_factory() -> Callable[..., tuple[dict[str, Any], FakeLambdaContext]]:
    """Expose the invocation factory as a fixture for readability in tests."""
    return create_invocation


@pytest.fixture(autouse=True)
def reset_service() -> Generator[None]:
    """Keep the module-level service singleton from leaking between tests."""
    TodoService._store = None
    yield
    TodoService._store = None


@pytest.fixture
def wired_store(aws_credentials: None) -> Generator[None]:
    """Back the handler with a moto table so tool calls reach real validation."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "item_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "item_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        TodoService.setup(TodoStore(table_name=TABLE_NAME, dynamodb_client=client))
        yield


@pytest.fixture
def aws_credentials() -> Generator[None]:
    """Point botocore at fake credentials so moto never touches real AWS."""
    originals = {
        key: os.environ.get(key)
        for key in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SECURITY_TOKEN",
            "AWS_SESSION_TOKEN",
            "AWS_DEFAULT_REGION",
        )
    }
    os.environ.update(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",  # noqa: S106 — deliberately fake, so moto never reaches real AWS
        AWS_SECURITY_TOKEN="testing",  # noqa: S106
        AWS_SESSION_TOKEN="testing",  # noqa: S106
        AWS_DEFAULT_REGION="us-east-1",
    )
    yield
    for key, value in originals.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


# --------------------------------------------------------------------------
# The deployed stack.
#
# Everything above substitutes AWS; everything below reaches the real thing and
# is therefore marked `aws` and deselected by default. The fixtures live in this
# shared conftest rather than beside one test because both layers that reach a
# deployed stack need them: the gateway tests in integration_tests/, which stop
# short of the model, and the conversation tests in e2e/, which do not.
#
# Nothing here fails when the stack is unreachable. A fresh clone has no .env
# and a laptop without a profile has no credentials; neither is a defect in the
# code under test, so both skip.
# --------------------------------------------------------------------------

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


@dataclass(frozen=True)
class DeployedStack:
    """What the deployed stack has to say about itself before a test can reach it."""

    region: str
    gateway_url: str
    table_name: str
    runtime_arn: str

    @classmethod
    def from_env_file(cls, path: Path) -> DeployedStack:
        """Read the configuration out of the .env file itself.

        Deliberately not `os.environ`: `[tool.pytest_env]` in pyproject.toml
        already sets GATEWAY_URL and the rest to stand-ins, so that
        todo_runtime can be imported at all, and it sets them over whatever the
        shell exported. Reading the file directly keeps the deployed values and
        the offline stand-ins from being the same names in one process.
        """
        values = dotenv_values(path)
        return cls(
            region=values.get("AWS_REGION") or "",
            gateway_url=values.get("GATEWAY_URL") or "",
            table_name=values.get("DYNAMODB_TABLE_NAME") or "",
            runtime_arn=values.get("AGENT_RUNTIME_ARN") or "",
        )

    def missing(self) -> list[str]:
        """Return the names of the settings that are absent or empty."""
        present = {
            "AWS_REGION": self.region,
            "GATEWAY_URL": self.gateway_url,
            "DYNAMODB_TABLE_NAME": self.table_name,
            "AGENT_RUNTIME_ARN": self.runtime_arn,
        }
        return [name for name, value in present.items() if not value]


@pytest.fixture(scope="session")
def deployed() -> DeployedStack:
    """The deployed stack's coordinates, or a skip explaining how to get them."""
    if not ENV_FILE.exists():
        pytest.skip(f"{ENV_FILE.name} is absent; run scripts/sync_env.sh")

    config = DeployedStack.from_env_file(ENV_FILE)
    if missing := config.missing():
        pytest.skip(f"{ENV_FILE.name} carries no {', '.join(missing)}; rerun scripts/sync_env.sh")
    return config


@pytest.fixture(scope="session")
def aws_session(deployed: DeployedStack) -> boto3.Session:
    """A boto3 session with real credentials, or a skip."""
    session = boto3.Session(region_name=deployed.region)
    if session.get_credentials() is None:
        pytest.skip("no AWS credentials; export AWS_PROFILE")
    return session


@pytest.fixture(scope="session")
def gateway(deployed: DeployedStack, aws_session: boto3.Session) -> GatewayClient:
    """The production MCP client, pointed at the deployed gateway.

    The client under test rather than a test-local reimplementation: the
    handshake, the SigV4 signing and the SSE unwrapping are exactly what
    test_mcp.py can only check against a substituted urlopen.
    """
    return GatewayClient(
        url=deployed.gateway_url,
        region=deployed.region,
        credentials=aws_session.get_credentials(),
    )


@pytest.fixture(scope="session")
def tool_names(gateway: GatewayClient) -> dict[str, str]:
    """Bare tool name to the qualified name the gateway publishes it under."""
    return {name.rpartition(TOOL_NAME_SEPARATOR)[2]: name for name in (tool["name"] for tool in gateway.list_tools())}


@pytest.fixture(scope="session")
def table(deployed: DeployedStack, aws_session: boto3.Session) -> Any:
    """The real items table, read directly.

    Assertions go through boto3 rather than through TodoStore on purpose: a
    test that reads back with the same code that wrote would confirm itself.
    """
    return aws_session.resource("dynamodb").Table(deployed.table_name)


@pytest.fixture
def make_user(table: Any) -> Generator[Callable[[], str]]:
    """Hand out throwaway user ids and delete everything they own afterwards.

    Every store in the stack partitions by user_id, so a random one is a
    private corner of the real table: these tests can run beside a live
    conversation without either seeing the other.
    """
    created: list[str] = []

    def _make() -> str:
        identity = f"pytest-{uuid.uuid4()}"
        created.append(identity)
        return identity

    yield _make

    for identity in created:
        items = table.query(KeyConditionExpression=Key("user_id").eq(identity)).get("Items", [])
        with table.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"user_id": identity, "item_id": item["item_id"]})


@pytest.fixture
def user_id(make_user: Callable[[], str]) -> str:
    """One throwaway user, for the tests that need only one."""
    return make_user()


@pytest.fixture
def call(gateway: GatewayClient, tool_names: dict[str, str]) -> Callable[..., dict[str, Any]]:
    """Call a tool by its bare name, injecting identity the way the runtime does.

    `user_id=None` omits it entirely, which is how the refusal path is reached.
    """

    def _call(tool: str, user_id: str | None, **arguments: Any) -> dict[str, Any]:
        payload: dict[str, Any] = dict(arguments)
        if user_id is not None:
            payload[USER_ID_KEY] = user_id
        return gateway.call_tool(tool_names[tool], payload)

    return _call


# A turn that calls tools runs several model round trips, and the runtime holds
# the connection open for all of them. scripts/chat.py waits exactly this long.
TURN_READ_TIMEOUT = 120


@dataclass(frozen=True)
class Turn:
    """One conversation turn, collected off the wire instead of printed."""

    events: list[dict[str, Any]]

    @property
    def text(self) -> str:
        """Everything the agent said, in order."""
        return "".join(str(event.get("text", "")) for event in self.events if event.get("type") == "text")

    @property
    def tools(self) -> list[str]:
        """The tools the model chose, bare-named, in the order it chose them."""
        return [
            str(event["name"]).rpartition(TOOL_NAME_SEPARATOR)[2]
            for event in self.events
            if event.get("type") == "tool_use"
        ]

    @property
    def errors(self) -> list[str]:
        """Error frames the runtime sent. Empty is what a working turn looks like."""
        return [str(event.get("message", "")) for event in self.events if event.get("type") == "error"]

    @property
    def completed(self) -> bool:
        """Whether the stream ended the way the contract says it should."""
        return bool(self.events) and self.events[-1].get("type") == "done"


@pytest.fixture(scope="session")
def agentcore(deployed: DeployedStack, aws_session: boto3.Session) -> Any:
    """A bedrock-agentcore client configured the way scripts/chat.py configures its own."""
    return aws_session.client(
        "bedrock-agentcore",
        region_name=deployed.region,
        config=Config(connect_timeout=5, read_timeout=TURN_READ_TIMEOUT, retries={"max_attempts": 2}),
    )


@pytest.fixture
def conversation(agentcore: Any, deployed: DeployedStack, user_id: str) -> Callable[[str], Turn]:
    """Speak to the deployed agent as one user, in one session, turn after turn.

    Invocation and frame parsing come from scripts/chat.py rather than from a
    copy written here: the client is part of what these tests are for, and a
    second implementation could be wrong in its own way and still agree with
    itself.

    The session id is a uuid because AgentCore requires at least 33 characters
    for one, and a shorter label is refused with a validation error that says
    nothing about the turn.
    """
    session_id = str(uuid.uuid4())

    def _say(prompt: str) -> Turn:
        return Turn(list(stream_turn(agentcore, deployed.runtime_arn, user_id, session_id, prompt)))

    return _say

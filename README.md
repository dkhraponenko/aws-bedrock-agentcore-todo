# todo-agent

A natural-language todo list built on **Amazon Bedrock AgentCore** with Claude.
A sentence like *"add a new item to the list, buy a milk"* becomes a tool call;
the call travels through an **AgentCore Gateway** to a **Lambda**, which reads
and writes **DynamoDB**. All infrastructure is Terraform.

```
  "delete buy a milk"
          |
          v
  +-------------------+     managed orchestration loop: model,
  |  AgentCore        |     system prompt, tools, max_iterations
  |  harness (Claude) |
  +---------+---------+
            | bedrock-agentcore:InvokeGateway
            v
  +-------------------+     MCP facade; the tool contract the
  |  AgentCore        |     model sees lives on the target
  |  Gateway + target |
  +---------+---------+
            | lambda:InvokeFunction (gateway's own role)
            v
  +-------------------+     one tool call in, one JSON result out
  |  Lambda           |
  |  todo_agent       |
  +---------+---------+
            | Query / PutItem / UpdateItem / DeleteItem
            v
  +-------------------+     PK = user_id, SK = item_id
  |  DynamoDB         |     one partition per user, no Scans
  +-------------------+
```

## The five tools

Declared inline in `infrastructure/agentcore.tf`, so the contract the model
sees has no second source of truth.

| Tool | Parameters | Purpose |
|---|---|---|
| `add_item` | `text*`, `priority` | Create a task |
| `list_items` | `status` | Return the whole list |
| `search_items` | `query*`, `status` | Find tasks **and their `item_id`** |
| `update_item` | `item_id*`, `text`, `status` | Change text and/or status |
| `delete_item` | `item_id*` | Remove a task |

`* = required`

### Why `update_item` and `delete_item` only take an `item_id`

They never accept free text. A request phrased in natural language has to be
resolved through `search_items` first, which is what produces the multi-step
trace worth looking at:

```
you   > delete buy a milk

  -> search_items {"query": "milk"}
     {"count": 1, "items": [{"item_id": "01991f2c3a4b1c2d3e", "text": "buy a milk", ...}]}
  -> delete_item {"item_id": "01991f2c3a4b1c2d3e"}
     {"deleted": true, "item": {...}}

agent > Done — I removed "buy a milk" from your list.
```

Keeping resolution in the model rather than the Lambda leaves the tool contract
deterministic, and it means ambiguity ("you have two tasks mentioning milk")
is handled where the user can be asked about it.

## Layout

```
infrastructure/       Terraform, flat root — one file per concern
  providers.tf        versions, provider, caller identity
  variables.tf        project name, region, model, sizing
  dynamodb.tf         the items table
  lambda.tf           packaging, function, log group
  iam.tf              Lambda, gateway and harness roles
  agentcore.tf        gateway, target with the five tools, harness
  outputs.tf

src/todo_agent/       Lambda source — this directory is the deployment package
  lambda_handler.py   entry point; TodoService.setup() runs at import (cold start)
  service.py          parse invocation -> dispatch -> plain JSON result
  store.py            DynamoDB access; every read is a Query
  models.py           TodoItem, TodoStatus, ToolInvocation
  errors.py           domain errors surfaced to the model

scripts/chat.py       interactive InvokeHarness client that prints the tool trace
tests/                unit tests on mocks, integration tests on moto
```

## Design notes

**The gateway's Lambda contract is thinner than it looks.** The event *is* the
tool's arguments — a flat JSON object, with the types declared in the tool
schema preserved. The tool name arrives out of band, in
`context.client_context.custom['bedrockAgentCoreToolName']`, prefixed with the
target name (`todo___add_item`). Going back, the return value is JSON-encoded
into an MCP text content block — `[{"text": "{\"created\":true,...}"}]` — and
that is the whole envelope: no status field, no success flag. A failure is
reported as an ordinary result carrying an `error` key, and the model decides
what to do with it.

**No build step.** The function imports nothing beyond the standard library and
`boto3`, which the Lambda runtime already provides. `archive_file` zips `src/`
directly, so `terraform plan` works straight after `git clone` — no
`pip install -t`, no layer, no `manylinux` wheels.

**Ownership is the key, not a check.** `user_id` is the partition key, so a
lookup for another user's `item_id` simply misses. There is no "fetch, then
verify owner" step that could be dropped.

**Least-privilege IAM, three roles.** The Lambda role grants exactly the five
DynamoDB actions the store makes, on exactly one table. The gateway role grants
`lambda:InvokeFunction` on exactly one function — and since that role is the
upper bound on everything reachable through the gateway, keeping it to one
action matters. The harness role grants model invocation,
`bedrock-agentcore:InvokeGateway` on one gateway, and read/append access to the
conversation memory the harness creates for itself on first invocation — that
memory is not a Terraform resource, so it is scoped by the `harness_*` name
prefix rather than by ARN. Both AgentCore trust policies
carry an `aws:SourceAccount` condition against confused-deputy abuse — but only
that one: `CreateGatewayTarget` validates the role by assuming it, and that
call carries no `aws:SourceArn`, so adding the usual `ArnLike` companion
condition makes target creation fail with *"Gateway service is not authorized
to perform AssumeRole on Gateway role"*.

**Inbound auth is `AWS_IAM`, not `CUSTOM_JWT`.** The harness reaches its tools
by calling the gateway as itself, which keeps authorization a pure IAM problem.
`CUSTOM_JWT` would drag in Cognito or another OIDC provider for what is a
single-operator stand.

**No resource policy on the Lambda.** The gateway invokes it by assuming its
own role — a same-account, identity-based grant. An `aws_lambda_permission`
would be a second place to audit for the same decision.

**The model is reached through an inference profile.** Current Claude models on
Bedrock are only invokable via a cross-region inference profile, which is why
`var.agent_model` carries the `us.` prefix and IAM grants the profile ARN
*and* the underlying `foundation-model/*` ARN in every region the profile can
route to.

## Running it

```bash
# Static checks and tests
python -m venv .venv && .venv/bin/pip install \
  pytest pytest-env pytest-cov 'moto[dynamodb]' boto3 mypy ruff

ruff check . && ruff format --check .
.venv/bin/mypy src scripts tests
.venv/bin/pytest --cov

# Infrastructure
cd infrastructure
terraform init
terraform validate
terraform plan          # needs AWS credentials; validate does not
```

Deploying:

```bash
terraform apply
eval "$(terraform output -raw chat_command)"
```

### Model access

Serverless foundation models enable themselves on first invocation — the old
**Bedrock → Model access** page has been retired — but **Anthropic models are
gated behind a one-time use-case form plus a per-model agreement**. Until both
are done every call fails with `AccessDeniedException`, worded as though the
model did not exist. Diagnose it with:

```bash
aws bedrock get-foundation-model-availability \
  --model-id anthropic.claude-sonnet-5 --region us-east-1
```

Four independent flags come back — entitlement, region, IAM authorization and
agreement — which says immediately which one is missing. Submit the form under
**Bedrock → Model catalog** in the console; `agreementAvailability` then flips
to `AVAILABLE`.

Amazon Nova requires neither step, so copying `terraform.tfvars.example` to
`terraform.tfvars` deploys the whole stack against `us.amazon.nova-pro-v1:0`
while Anthropic access is pending. `var.agent_model` is the only knob; nothing
else in the stack is model-specific.

## Not done here

- **Single-user.** Nothing carries a caller identity down to the Lambda, so
  `user_id` is a constant. Doing it properly would mean either an extra tool
  parameter (which the model should not be choosing) or `CUSTOM_JWT` inbound
  auth with the caller's identity forwarded to the target.
- **No CI.** `ruff`, `mypy` and `pytest` are configured in `pyproject.toml` but
  not wired to a workflow.
- **No remote state.** State is local, which is fine for a single-operator
  stand and wrong for a team.
- **No AgentCore memory.** `aws_bedrockagentcore_memory` would give the harness
  cross-session recall; the CLI script keeps one session id instead.

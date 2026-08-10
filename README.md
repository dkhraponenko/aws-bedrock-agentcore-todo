# todo-agent

A natural-language todo list built on **Amazon Bedrock AgentCore** with Claude.
A sentence like *"add a new item to the list, buy a milk"* becomes a tool call;
the call travels through an **AgentCore Gateway** to a **Lambda**, which reads
and writes **DynamoDB**. All infrastructure is Terraform.

<img src="docs/architecture.svg" alt="AgentCore harness and gateway in front of a Lambda and DynamoDB" width="900">

Each hop is a separate IAM identity: the harness assumes its role to call the
model and the gateway, the gateway assumes its own to invoke the Lambda, and
the Lambda assumes a third to touch the table. No hop can reach past the next
one.

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

## What it costs

Rates below are us-east-1 on-demand, pulled from the AWS Price List API on
**9 August 2026** (`aws pricing get-products`), not from a pricing page.

| Component | Unit rate |
|---|---|
| Nova Pro tokens | $0.80 / 1M in, $3.20 / 1M out |
| Claude Sonnet 5 tokens | $3.00 / 1M in, $15.00 / 1M out — $2.00 / $10.00 introductory through 31 Aug 2026 |
| AgentCore Gateway | $0.000005 per tool invocation |
| AgentCore Gateway tool indexing | $0.0002 per tool per month |
| AgentCore short-term memory | $0.00025 per event stored |
| Lambda | $0.20 / 1M requests + $0.0000166667 per GB-second |
| DynamoDB on-demand | $0.625 / 1M writes, $0.125 / 1M reads, $0.25 per GB-month |
| CloudWatch Logs | $0.50 per GB ingested, $0.03 per GB-month stored |

Costing one conversation: **10 user turns, one tool call each**. That is two
model calls per turn (pick the tool, then answer from its result), ~25K input
tokens in total because the system prompt and the five tool schemas are resent
every call, and ~1.5K output tokens.

| Line item | Quantity | Nova Pro |
|---|---|---|
| Model input | 25K tokens | $0.0200 |
| Model output | 1.5K tokens | $0.0048 |
| Memory events | ~40 | $0.0100 |
| Gateway invocations | 10 | $0.0001 |
| Lambda + DynamoDB + Logs | 10 calls | $0.0000 |
| **Total** | | **~$0.035** |

The same conversation on Claude Sonnet 5 is **~$0.075** at introductory rates
and **~$0.11** after — the non-model half of the bill does not move.

**Idle cost is effectively zero.** Everything except gateway tool indexing
(5 tools × $0.0002 = **$0.001/month**) is billed per request, and a few KB in
DynamoDB sits inside the always-free 25 GB. Leaving the stack deployed between
demos costs a tenth of a cent a month; a `destroy`/`apply` cycle is not worth
the trouble.

Two things worth noticing in that table. **Memory is a third of the bill** —
$0.00025 per event is small until you notice every user message, assistant
message, tool call and tool result is one, so conversation length drives it
quadratically alongside the resent context. And **the tool plumbing rounds to
zero**: Lambda, DynamoDB and the gateway together cost less than 0.5% of a
conversation. Tokens are the entire cost model here.

One caveat: the Price List API has no separate SKU for harness orchestration,
so the numbers above assume the loop itself is not billed beyond the model,
gateway and memory usage it generates. Verify against a real bill before
quoting these for anything that matters.

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

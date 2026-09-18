# todo-agent

A natural-language todo list on Amazon Bedrock AgentCore. *"add a new item to
the list, buy a milk"* becomes a tool call that travels through an AgentCore
Gateway to a Lambda, which reads and writes DynamoDB. All of it is Terraform.

<img src="docs/architecture.svg" alt="An AgentCore Runtime loop calling tools through a gateway into a Lambda and DynamoDB" width="1000">

```
you   > delete buy a milk

  -> search_items {"query": "milk"}
     {"count": 1, "items": [{"item_id": "19fe7ec61e693a7fd", "text": "buy a milk", ...}]}
  -> delete_item {"item_id": "19fe7ec61e693a7fd"}

agent > Done — I removed "buy a milk" from your list.
```

Deleting by name takes two calls because the tools take ids and nothing else.
That keeps the tool contract deterministic and puts ambiguity — "two tasks
mention milk" — where the user can be asked about it.

**Why the reasoning behind all this lives elsewhere:** the decisions, the
platform contracts and the cost model are in
[docs/design-notes.md](docs/design-notes.md).

## The five tools

They live in `infrastructure/tools.json`. Terraform expands that file into the
gateway target, and a test checks it against the handlers. At run time the agent
asks the gateway what it publishes rather than reading the file, so the model
sees exactly what the gateway will accept.

| Tool | Parameters | Purpose |
|---|---|---|
| `add_item` | `text*`, `priority` | Create a task |
| `list_items` | `status` | Return the list, up to 100 items |
| `search_items` | `query*`, `status` | Find tasks **and their `item_id`** |
| `update_item` | `item_id*`, `text`, `status` | Change text and/or status |
| `delete_item` | `item_id*` | Remove a task |

`* = required`

## How it works

`scripts/chat.py` sends one turn to AgentCore Runtime. The runtime starts the
agent as an ordinary process and talks HTTP to it, so `src/main.py` launches a
small server, and `todo_runtime` does the rest: load the conversation from
AgentCore Memory, call Nova Pro, and act on whatever tools it asks for. Tool
calls go over MCP to the gateway, which invokes the `todo_agent` Lambda, which
reads and writes DynamoDB. Answers stream back to the terminal as they are
produced.

There are three Python packages: `todo_agent` is the tool Lambda, `todo_runtime`
is the agent loop and the server that hosts it, and `todo_logging` is a JSON log
formatter that ships inside both.

Every hop is a separate IAM identity. The runtime calls the model and the
gateway, the gateway invokes the Lambda, the Lambda touches the table. No hop
can reach past the next one.

## Isolation

`user_id` is the DynamoDB partition key, so one user's `item_id` simply misses
in another user's partition. There is no "read it, then check the owner" step
that could get dropped.

The identity comes from the caller and is injected into every tool call by the
loop. The gateway does not publish it as a parameter, so the model never sees
one and cannot supply one that survives. A call arriving without an identity is
refused rather than defaulted — a default would quietly merge every caller into
one partition.

The loop runs on AgentCore Runtime instead of the managed harness for exactly
this reason: the harness forwards nothing about the end user to its tools, so
every caller would share one list.

## Security

One role per hop, each with one grant: five DynamoDB actions on one table,
`lambda:InvokeFunction` on one function, and for the runtime, model invocation
plus `InvokeGateway` and two memory calls. The gateway's single action is the
upper bound on everything reachable through it.

There is no public surface: no function URL, no API Gateway, no Lambda resource
policy. The gateway is the only caller, in the same account.

Model output is untrusted — every argument is revalidated in the Lambda, and
writes are conditional. Prompt injection is bounded rather than solved: item
text reaches the model, but the identity does not, so the worst case stays
inside the attacker's own partition.

Logs carry ids, never item text, and expire after 14 days. The table has SSE and
point-in-time recovery, and there are no secrets in the stack.

## What it costs

A ten-turn conversation with a tool call in each turn is about **$0.035**, and
almost all of it is model tokens: the system prompt and five tool schemas are
resent on every call. Lambda, DynamoDB and the gateway together are under 0.5%
of that.

Idle cost is tool indexing plus a few KB in S3, about **$0.001/month**. The one
thing worth knowing is that Runtime bills for a session's lifetime rather than
for time spent working, which is what `var.runtime_idle_timeout_seconds` is for.

Rates, assumptions and the line-by-line breakdown are in
[docs/design-notes.md](docs/design-notes.md#what-it-costs-in-detail).

## Running it

```bash
python3.13 -m venv .venv && .venv/bin/pip install --group dev

pre-commit install --install-hooks && pre-commit install --hook-type pre-push
pre-commit run --all-files    # everything the hooks enforce, in one go

.venv/bin/pytest              # tests + the coverage gate

scripts/build_runtime.sh      # vendors boto3 into the runtime artifact

cd infrastructure
terraform init                # state is in S3, so credentials from here on
terraform apply
cd ..

scripts/sync_env.sh           # terraform outputs -> .env
python scripts/chat.py
```

`.env` is a gitignored cache of the terraform outputs; `.env.example` is the
committed shape, and `sync_env.sh` rewrites it after every apply. Put
`AWS_PROFILE=...` in it and every script and test picks it up. Anything already
exported wins, so `USER_ID=bob python scripts/chat.py` switches user for one run.

Terraform state lives in S3. The bucket, the OIDC provider and the deploy role
come from `infrastructure/bootstrap/`, which is applied once by hand.

## Tests

```bash
.venv/bin/pytest                                        # offline, no credentials
AWS_PROFILE=... .venv/bin/pytest -m aws --no-cov        # against the deployed stack
```

The directory says how much of the system a test covers: `tests/unit/`
substitutes everything underneath, `tests/integration_tests/` wires real parts
together, `tests/e2e/` runs a whole chat turn from `scripts/chat.py` to
DynamoDB. The `aws` marker says whether a deployed stack is needed, and the
default run excludes it.

Between deploys, `scripts/local_agent.py` runs the same loop against the real
model with the gateway replaced by a local dispatcher — a few cents, and it
deploys nothing.

Hooks run ruff, mypy and `terraform fmt`/`validate` on commit, and the suite
behind a 95% branch-coverage floor on push (currently 99%). The same hooks run
in GitHub Actions on every push and pull request, with tflint and trivy added.
Deploying is a separate workflow behind a button.

## Not done here

- **No login.** The stack partitions by `user_id`, but the only client is
  `scripts/chat.py`, which sends whatever `USER_ID` says while authenticating to
  AWS as the operator. The isolation is real; what is missing is something that
  establishes who the user is, such as a web client behind Cognito.
- **Short-term memory only.** No extraction strategies, so there is session
  history and no recall across conversations.
- **Not load-tested.** One vCPU and the default concurrency were never measured;
  the cost model assumes a session shape rather than an observed one.
- **Priority is write-once.** `add_item` takes one and `update_item` has no
  field for it, so "make that one priority 1" cannot be honoured — and asked to
  do it anyway, the model emits a malformed tool call and the turn dies.

## License

MIT, in `LICENSE`.

The code, not the pictures: the service glyphs in `docs/architecture.svg` are the
official AWS Architecture Icons, inlined unmodified and still AWS's. The pack
itself is not redistributed here. `docs/architecture_diagram.py` is the spec the
picture is generated from; regenerating it needs the renderer that carries the
icons, and the SVG is committed so that reading the repository does not.

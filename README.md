# agent-orchestrator

A long-running Python service that drives a Claude agent fleet against any
backlog file, unattended. Give it a list of tasks — coding tickets, marketing
deliverables, research questions, anything that fits the "ready → done" model
— and it cycles through them, one task per cycle, dispatching the work to a
fresh Claude session with the right context, then handing the result off to
a configurable "shipper" (open a PR, save a deliverable, just record).

```
   ┌────────────────────────────────────────────────────────────┐
   │ outer orchestrator (this service)                          │
   │                                                            │
   │  loop:                                                     │
   │    workspace.prepare()           ← reset / snapshot        │
   │    before = backlog.snapshot()                             │
   │    skip = shipper.in_flight_slugs()                        │
   │    spawn Claude session ───────────────────────┐           │
   │      cwd = workspace dir                       │           │
   │      prompt = your cycle template (with skip)  │           │
   │    after = backlog.snapshot()                  │           │
   │    if a task flipped to Done AND files changed:│           │
   │      shipper.ship(task, workspace) ─→ PR / dir │           │
   │    else: workspace.reset_after_no_progress()   │           │
   │                                                ↓           │
   │  watch loop (concurrent):                                  │
   │    shipper.watch()  ← polls PR CI, finalizes shipments     │
   └────────────────────────────────────────────────────────────┘
```

## Documentation

- **[Getting started](docs/getting-started.md)** — install, smoke-test, run
  your first cycle in ten minutes.
- **[Configuration reference](docs/configuration.md)** — every section,
  every field, every default.
- **[Extending](docs/extending.md)** — write a custom backlog source,
  workspace, or shipper.
- **[Operations](docs/operations.md)** — deploy as a service, monitor,
  troubleshoot, recover from failure.
- **[Contributing](CONTRIBUTING.md)** — dev setup, tests, what's easy /
  what needs discussion.

## What's pluggable

| Concept | What it controls | Built-in implementations |
|---|---|---|
| `BacklogProvider` | Where tasks come from, what counts as "done" | `MarkdownBacklog` (configurable status field + done values) |
| `Workspace` | The directory the agent works inside | `GitWorkspace` (persistent clone, branch per cycle), `DirectoryWorkspace` (plain dir, snapshot diff) |
| `Shipper` | What happens when a cycle produces output | `GitPRShipper` (commit → push → PR → CI watch → squash-merge), `DirectoryShipper` (copy outputs into `deliverables/<slug>/`), `NoopShipper` |
| Prompt template | What you ask the agent to do each cycle | `examples/<flavour>/prompts/cycle.md` — author your own |

The pieces are mixed and matched in `config.toml`. Three example configs ship
in `examples/`:

- **[examples/coding/](examples/coding/)** — git workspace + git_pr shipper.
  Same flow as the `phone-booking-agent` orchestrator this repo was
  generalized from.
- **[examples/marketing/](examples/marketing/)** — directory workspace +
  directory shipper. Uses `Stage` instead of `Status`, `Published` instead
  of `Done`. Outputs land in `./deliverables/<slug>/`.
- **[examples/research/](examples/research/)** — directory workspace + noop
  shipper. The agent edits the backlog itself (e.g. answers questions in
  place); no separate delivery step.

## Backlog schema

The default `MarkdownBacklog` parser expects this shape (the field names are
configurable — `status_field`, `done_values`, `acceptance_field`):

```markdown
### my-task-slug: Free-text title

- **Status:** Ready
- **Priority:** P1
- **Owner:** developer
- **Depends-On:** other-slug
- **Acceptance:**
  - bullet list
  - of acceptance criteria
- **Result:** (filled in by the agent when done)
```

Anything between an `### slug:` heading and the next `### `, `## `, or `---`
is the task's block. Field values are case-insensitive on the field name.

See [docs/configuration.md#backlog-schema-markdown](docs/configuration.md#backlog-schema-markdown)
for the full schema.

## Quickstart

```bash
git clone https://github.com/autonomousagentai/agent-orchestrator.git
cd agent-orchestrator
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .[git]              # drop the [git] extra if you only need
                                   # directory / noop shippers

# Pick a flavour and copy its config + prompt + sample backlog.
cp examples/coding/config.toml.example  ./config.toml
cp -r examples/coding/prompts           ./prompts
cp .env.example                         ./.env
$EDITOR config.toml .env

# Run a single cycle (smoke test).
agent-orchestrator --once

# Or run the full loop.
agent-orchestrator
```

For a step-by-step walkthrough including expected output and common
gotchas, see [docs/getting-started.md](docs/getting-started.md).

## The CYCLE_DONE handshake

Your prompt template should ask the agent, at the very end, to print one
line:

    CYCLE_DONE slug=<task-slug> outcome=<done|sent-back|no-task|halted>

The orchestrator parses that line to know which task to ship. If the line is
missing or malformed, the orchestrator falls back to "any task whose status
flipped to Done in the backlog diff" — but the explicit handshake is more
reliable, so include it in your prompt.

The marker word is configurable via `agents.cycle_done_marker`.

## Cost guardrails

Three layers, smallest first:

1. `agents.max_turns` — caps the inner agent loop length per single cycle.
2. `orchestrator.cycle_budget_usd` — hard cap on per-cycle spend. The
   actual budget passed to the SDK is `min(cycle_budget, daily_budget -
   today_spent)`, so a near-exhausted day automatically tightens.
3. `orchestrator.daily_budget_usd` — orchestrator stops dispatching when
   today's UTC spend hits this. The watch loop keeps running so in-flight
   shipments can still finalize.

Plus: `orchestrator.no_progress_pause_after` halts the cycle loop after N
cycles in a row that produce no shippable output. Usually that means the
backlog is exhausted, or the agent keeps bouncing the same task; either way
the orchestrator stops burning tokens until you check on it.

## Sandboxing

By default the orchestrator installs two PreToolUse hooks on every cycle:

- A **path guard** that rejects any `Read`/`Write`/`Edit`/`MultiEdit`/
  `NotebookEdit` whose target path resolves outside the workspace.
- A **bash guard** that blocks a small denylist of clearly-destructive
  shell commands (`rm -rf /`, fork bombs, `mkfs`, `shutdown`) and — on a
  `GitWorkspace` — refuses any `git push` that targets the protected base
  branch.

These are belt-and-suspenders alongside whatever permissions you set in
`agents.permission_mode` and `agents.allowed_tools`.

## Operating it

- **Inspect cycles:** `agent-orchestrator-status` (or `--watch`).
- **Tail logs:** `tail -f logs/orchestrator.log`, plus per-cycle
  transcripts at `logs/cycle-N.jsonl`.
- **Pause:** `Ctrl-C` (or `systemctl stop`). The current cycle finishes
  before exit.
- **Force a re-clone** (git workspace, after token rotation): stop the
  service, `rm -rf workspaces/repo`, restart.
- **Tweak budgets:** edit `config.toml`, restart.

For deploying as a long-lived service, monitoring, restart safety, and
disaster recovery, see [docs/operations.md](docs/operations.md).

## Origin

This repo generalizes the cycle-based orchestrator from the
[phone-booking-agent](https://github.com/autonomousagentai/phone-booking-agent)
project's `orchestrator/` directory. That implementation was tightly coupled
to a coding-project BACKLOG.md and a GitHub-PR-shipping flow; here the same
machinery is split into pluggable backlog/workspace/shipper layers so the
same loop can drive non-code work too.

## Status

Pre-1.0. The interfaces (`BacklogProvider`, `Workspace`, `Shipper`) are
deliberately minimal and we'd rather change them than carry compatibility
shims for early users. Pin to a specific commit if you're embedding this in
a long-lived deployment.

## License

[MIT](LICENSE)

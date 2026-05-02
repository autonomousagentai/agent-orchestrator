# dev-ops-agents-orchestrator

A long-running Python service that **watches your other autonomous orchestrators**
and unblocks them when they get stuck. If one of your `coding`, `marketing`, or
`research` agent fleets halts, errors, or stops making progress, this fleet
finds the cause, patches the affected repo, pushes the fix, and the watched
fleet picks back up where it left off.

Built on the
[agent-orchestrator](https://github.com/autonomousagentai/agent-orchestrator)
boilerplate — same `BacklogProvider` / `Workspace` / `Shipper` machinery, with
a dev-ops-specific cycle prompt and a four-agent specialist roster.

```
   ┌────────────────────────────────────────────────────────────────┐
   │ dev-ops-agents-orchestrator (this service)                     │
   │                                                                │
   │  every cycle:                                                  │
   │    1. monitor      → sweeps incoming-logs/<org>/ for           │
   │                       errors, halts, stuck cycles, CI failures │
   │                       → appends new entries to INCIDENTS.md    │
   │    2. pick highest-priority Ready incident                     │
   │    3. diagnostician → reads transcript + repo, isolates cause  │
   │    4. fixer        → patches monitored/<org>/, pushes branch + │
   │                       opens PR on the affected repo            │
   │    5. verifier     → re-runs the failing cycle / runs tests,   │
   │                       APPROVED or SEND-BACK                    │
   │    6. flip incident Status → Resolved + write fix report       │
   └────────────────────────────────────────────────────────────────┘
```

## What it watches

Anything that emits the standard agent-orchestrator artefact set:

- `logs/orchestrator.log` — top-level service log
- `logs/cycle-N.jsonl` — per-cycle Claude transcripts
- `state.db` — SQLite cycle/shipment state
- `agent-orchestrator-status` output — current cycle, in-flight slugs, halt reason

The monitor agent is told where to find these for each watched org via
`orchestrators.toml`. Logs can be pulled in via rsync, an S3 sync, a shared
volume mount, or any other mechanism — the orchestrator just reads them out of
`incoming-logs/<org>/`.

## What it knows how to fix

Common failure modes the diagnostician + fixer pair are prompted to handle:

- **Code bugs**: regression introduced by a recent agent PR; tests now red on
  `main`; the next cycle keeps tripping the same exception.
- **Config drift**: a missing or rotated env var, a changed CI workflow name,
  a moved file path in `config.toml`.
- **Prompt regressions**: the cycle prompt template is producing malformed
  `CYCLE_DONE` lines and the orchestrator falls back to no-task every cycle.
- **Workspace corruption**: half-applied merges, dangling lock files, a stale
  branch pointing at a force-pushed commit.
- **CI flakiness**: a known flaky test is blocking auto-merge; mark it
  quarantined with a follow-up issue rather than just retry forever.
- **Budget exhaustion**: today's `daily_budget_usd` is hit; either bump the
  cap (with explicit human approval, never autonomously) or file a
  `needs-human` escalation and stop.

Anything outside that envelope — credential rotations, new infra, anything
that requires a human decision — gets escalated to `INCIDENTS.md` with status
`Escalated` and `Owner: human`.

## The four agents

| Agent | Job |
|---|---|
| `monitor` | Read every watched org's recent logs + status output. Open new incidents in `INCIDENTS.md`. Deduplicate against open incidents. **Read-only on monitored repos.** |
| `diagnostician` | Reproduce + root-cause one incident. Output an `analysis.md` with the failing file:line, the reproducer, and the proposed fix. **Read-only on monitored repos.** |
| `fixer` | Apply the proposed fix inside `monitored/<org>/`. Run lints + tests. Commit, push a `dev-ops-fix/<incident-slug>` branch, open a PR. **Write access to monitored repos, scoped to that branch.** |
| `verifier` | Pull the fix branch, run the affected orchestrator's `--once` (or its tests), confirm the original symptom is gone. APPROVED → close incident. SEND-BACK → fixer iterates. |

Subagent definitions live in `.claude/agents/` so the cycle's `Task` tool
dispatches them with the right system prompt + tool scope.

## Quickstart

```bash
git clone https://github.com/<your-org>/dev-ops-agents-orchestrator.git
cd dev-ops-agents-orchestrator
python -m venv .venv
source .venv/bin/activate
pip install "agent-orchestrator[git] @ git+https://github.com/autonomousagentai/agent-orchestrator.git"

cp config.toml.example          ./config.toml
cp orchestrators.toml.example   ./orchestrators.toml
cp INCIDENTS.md.example         ./workspace/INCIDENTS.md
$EDITOR config.toml orchestrators.toml .env

# Smoke test: one cycle.
agent-orchestrator --once

# Production: run as a service.
agent-orchestrator
```

See the upstream
[getting-started](https://github.com/autonomousagentai/agent-orchestrator/blob/main/docs/getting-started.md)
guide for the install + smoke-test walk-through; everything in this repo is a
config + prompt layer on top of that.

## Watched orchestrators registry

`orchestrators.toml` lists every fleet this service monitors. Example entry:

```toml
[[orchestrator]]
name           = "coding-fleet"
repo           = "your-org/your-repo"
default_branch = "main"
logs_path      = "./incoming-logs/coding-fleet"
github_token   = { env = "GH_TOKEN_CODING" }
contacts       = ["@oncall-eng"]
fix_strategy   = "pr"          # or "push-direct" for trusted infra
```

The monitor reads it at the start of every cycle. New orchestrators can be
added without restarting the service.

## Safety

- The cycle's path-guard hook restricts file writes to the dev-ops workspace
  + the monitored clones. Nothing escapes.
- The bash-guard hook denies `git push --force` to any protected branch and
  refuses pushes that don't target a `dev-ops-fix/*` branch.
- Every `fixer` PR is labelled `dev-ops-autofix` so a human can audit the
  trail of who changed what.
- `verifier` is required before an incident closes. No silent self-approval.

## License

MIT (inherited from the upstream boilerplate).

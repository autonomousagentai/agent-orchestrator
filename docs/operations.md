# Operating agent-orchestrator

This guide covers running the orchestrator as a long-lived service: deploy,
monitor, troubleshoot, and recover.

---

## Deployment shapes

| Shape | When to use | Setup effort |
|---|---|---|
| Local foreground (`agent-orchestrator`) | Development, smoke tests, small overnight runs | None |
| Cron / `--once` | When you want exactly N cycles per day | Low |
| systemd unit | Production VPS, "always on" | Medium |
| Docker container | When you want strict isolation or k8s | Medium |

### Local foreground

```bash
source .venv/bin/activate
set -a; source .env; set +a    # load secrets into the shell
agent-orchestrator
```

Ctrl-C to stop; the in-flight cycle finishes first.

### Cron `--once`

For a "one cycle per hour, US business hours" schedule:

```cron
# m  h dom mon dow command
0 9-17 * * 1-5 cd /opt/agent-orchestrator && \
  /opt/agent-orchestrator/.venv/bin/agent-orchestrator --once \
  >> /opt/agent-orchestrator/logs/cron.log 2>&1
```

The `--once` mode runs a single cycle and exits. It will also process any
in-flight shipments before exiting (so a PR can finish merging even if
the cycle didn't dispatch new work).

### systemd

Copy the example:

```bash
sudo cp systemd/agent-orchestrator.service.example \
        /etc/systemd/system/agent-orchestrator.service
sudo systemctl daemon-reload
sudo systemctl enable --now agent-orchestrator
journalctl -u agent-orchestrator -f
```

Edit the unit if your install lives anywhere other than `/opt/agent-orchestrator`.
The unit:

- Runs as the `orchestrator` system user (create with `adduser --system`).
- Reads secrets from `/opt/agent-orchestrator/.env` via `EnvironmentFile=`.
- `Restart=always` with `RestartSec=10` — survives crashes.
- `TimeoutStopSec=600` — gives an in-flight cycle ten minutes to finish on
  `systemctl stop` before SIGKILL.
- Hardened with `ProtectSystem=strict`, `ProtectHome=true`, etc. The only
  writable path is `/opt/agent-orchestrator`.

### Docker

There's no published image, but the recipe is straightforward:

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e .[git]
ENV ORCHESTRATOR_CONFIG=/config/config.toml
CMD ["agent-orchestrator"]
```

Mount `/config` and `/app/{logs,workspaces,state.db}` as volumes so state
persists across container restarts.

---

## Monitoring

### Quick status

```bash
agent-orchestrator-status              # one-shot snapshot
agent-orchestrator-status --watch      # refresh every 5s
```

Both commands print:

- The most recent 20 cycles (id, status, cost, task slug, branch, PR/target).
- Today's UTC spend.

### Logs

| File | What's there |
|---|---|
| `logs/orchestrator.log` | One line per major event (cycle start, cycle finish, ship outcome, errors). |
| `logs/cycle-N.jsonl` | Full transcript of cycle N: every assistant message, every tool call, every result. JSON Lines — `jq .` on it. |
| journalctl (if systemd) | Same as `orchestrator.log` plus stdout/stderr. |

For day-to-day, `tail -f logs/orchestrator.log` is enough. When something
goes wrong, drop into `logs/cycle-<id>.jsonl` for the relevant cycle to see
exactly what the agent did.

### State database

`state.db` is a SQLite file you can poke at with `sqlite3`:

```sql
.headers on
.mode column

-- recent cycles
SELECT id, status, cost_usd, task_slug, pr_number, target_path
FROM cycles ORDER BY id DESC LIMIT 20;

-- this week's spend
SELECT day, ROUND(cost_usd, 2) AS usd
FROM daily_spend WHERE day >= date('now', '-7 days')
ORDER BY day DESC;

-- in-flight (PRs waiting for CI, etc.)
SELECT id, task_slug, pr_number, branch, started_at
FROM cycles WHERE status = 'in_flight';

-- cycles that errored, last 24h
SELECT id, task_slug, error
FROM cycles WHERE status = 'error' AND started_at > datetime('now', '-1 day')
ORDER BY id DESC;
```

### Cost tracking

The orchestrator records every cycle's `total_cost_usd` from the SDK's
`ResultMessage`. There are three levels of cost control:

1. **`agents.max_turns`** — caps the agent loop length per cycle. The
   `cycle_budget_usd` usually bites first.
2. **`orchestrator.cycle_budget_usd`** — passed to the SDK as
   `max_budget_usd`. The SDK refuses to start the next turn once spend
   crosses this.
3. **`orchestrator.daily_budget_usd`** — when today's UTC spend hits this,
   the cycle loop pauses dispatching new cycles. The watch loop keeps
   running so in-flight shipments can still finalize.

The actual budget passed to the SDK on each cycle is
`min(cycle_budget_usd, daily_budget_usd - today_spend)`, so a near-exhausted
day automatically tightens.

If spend looks higher than expected:

- Check `logs/cycle-N.jsonl` for the most expensive cycle. Often there's a
  loop where the agent keeps retrying the same failing tool call.
- Drop `agents.max_turns` (e.g. from 200 to 80) for tasks that should be
  short.
- Switch `agents.model` from sonnet to haiku for triage / simple tasks.

---

## Pause and resume

The orchestrator has three pause states, all automatic:

| Trigger | Behavior | Recovery |
|---|---|---|
| Daily budget hit | Cycle loop sleeps 5x `poll_interval_seconds`; watch loop continues. | Auto-resumes at 00:00 UTC when the daily counter rolls over. |
| `consecutive_no_progress` ≥ `no_progress_pause_after` | Cycle loop pauses indefinitely; watch loop continues. | Manual: investigate, then restart. |
| SIGTERM / SIGINT | Both loops stop after the current cycle/watch tick. | Restart the process. |

The "no progress" pause is the one you'll see most. Common causes:

1. **Backlog is exhausted** — no `Ready` tasks left. Add more, or stop the
   service.
2. **Backlog has a malformed task** — the agent picks it but can't make
   progress. Check the most recent `logs/cycle-N.jsonl`. Fix the backlog.
3. **The agent keeps hitting the same error** — out of disk, missing API
   keys for an MCP tool, etc. The transcript will show it.

To clear the pause without fixing the underlying issue (e.g. you fixed the
backlog and want to resume):

```bash
# Option 1: just restart. The pause counter is computed from the most recent
# cycles in state.db, so a fresh run with a non-empty backlog will produce
# a non-no_progress cycle and the counter resets.
sudo systemctl restart agent-orchestrator

# Option 2: delete the recent no_progress rows so the counter starts at 0
# without a fresh cycle.
sqlite3 state.db "DELETE FROM cycles WHERE status='no_progress' \
                   AND id > (SELECT MAX(id) - 10 FROM cycles)"
```

---

## Restart safety

The orchestrator is designed to survive crashes and restarts cleanly:

- **State**: SQLite with WAL journaling. A crash mid-write loses at most
  the in-progress row.
- **In-flight shipments**: on startup, `Orchestrator.__init__` reads any
  `in_flight` rows and re-seeds them into the shipper's watch queue (via
  `restore_in_flight`). PRs you opened before the crash will continue to
  be polled.
- **Workspace**: at the start of every cycle, `Workspace.prepare()` resets
  to a clean state. A crash mid-cycle leaves an orphan `cycle-<id>` branch
  on the git workspace; that gets cleaned up on the next reset.
- **Logs**: append-only. Rotated by your platform's logrotate / journald
  config — the orchestrator itself doesn't rotate.

What you DO need to handle yourself:

- **Token rotation**: stop the service, edit `.env`, restart. If the git
  workspace was using the old token in its remote URL, also `rm -rf
  workspaces/repo` so the next `prepare()` re-clones with the new token.
- **Config changes**: stop, edit `config.toml`, restart. Most changes pick
  up cleanly. `paths.*` changes are not migrated — if you move the db,
  copy `state.db` to the new location before restarting.

---

## Troubleshooting

### "GITHUB_TOKEN env var is required"

You set `workspace.type = git` or `shipper.type = git_pr` but the env var
isn't in the process environment. The CLI does NOT auto-load `.env`. For
foreground runs:

```bash
set -a; source .env; set +a
agent-orchestrator
```

For systemd, `EnvironmentFile=/path/to/.env` in the unit handles it.

### "config not found at /path/config.toml"

The CLI defaults to `./config.toml`. Use `-c <path>` or set
`ORCHESTRATOR_CONFIG=<path>` if your config is elsewhere.

### Cycle loop is silent for minutes

Two innocent causes, one nasty one:

- **Cloning the repo**: first run of a git workspace takes as long as
  `git clone` takes. Use `LOG_LEVEL=DEBUG` to see the shell commands.
- **Slow Anthropic responses**: Sonnet/Opus on large workspaces can take a
  couple minutes per cycle. Watch the JSONL transcript — you should see
  tool_use messages stream in.
- **A blocked hook**: if the agent's first tool call hits a path-guard
  deny, the agent might keep retrying. Check the transcript for repeated
  deny-reason messages.

### The agent runs but doesn't ship anything

Check `state.db`:

```sql
SELECT id, status, task_slug, error, notes
FROM cycles ORDER BY id DESC LIMIT 5;
```

`no_progress` means either:

- The task didn't flip to "done" in the backlog (the agent halted without
  marking it). Check the transcript's final assistant message.
- The task flipped to done but no files changed. Check
  `git status` (git workspace) or browse the workspace dir.

`error` means the shipper itself failed — usually a git op or a GitHub
API error. The `error` column has the message.

### CI is green but PR isn't merging

Watch loop logs:

```
INFO PR #123 ci=success checks=[('ci', 'success')]
INFO PR #123 not mergeable yet (mergeable_state=behind)
```

GitHub considers a PR "behind" when its base branch has new commits the
PR doesn't include. Two fixes:

- **Update the branch via the GitHub UI** — pulls the new base commits in.
  The next watch tick will see it as mergeable and merge.
- **Disable the "Require branches to be up to date before merging" branch
  protection rule** — the orchestrator will then squash-merge regardless
  of base drift.

### Hooks block legitimate operations

The path guard rejects file ops outside the workspace, and the bash guard
rejects `git push base_branch`. These are intentional defaults but you may
want them off in some contexts (e.g. a workspace where the agent should
read from a sibling directory).

To loosen, fork the repo and edit `agent_orchestrator/hooks/sandbox.py`.
The hooks are deliberately not config-driven — getting hook policy wrong
can let the agent escape the workspace, so we'd rather you make the change
in code where it gets reviewed.

### "I want to nuke everything and start fresh"

```bash
sudo systemctl stop agent-orchestrator
rm -rf logs workspaces state.db
sudo systemctl start agent-orchestrator
```

The next cycle will re-clone (git workspace), recreate the dirs, and start
counting from cycle 1.

---

## Disaster scenarios

### "I think the agent shipped a bad PR and CI auto-merged it"

The bash guard prevents pushing to the protected base branch from the
agent's session, but `git_pr` does merge via the GitHub API once CI is
green. Mitigations:

- **Branch protection**: server-side rules trump anything the orchestrator
  does. Require ≥ 1 review on the base branch — the orchestrator's
  squash-merge will then fail until a human approves.
- **`auto_squash_merge = false`**: the orchestrator opens PRs, watches CI,
  and labels them, but leaves the merge button to a human.
- **Audit**: `merged_label` (default `agent-merged`) tags every auto-merged
  PR; filter on it in the GitHub UI to review what shipped overnight.

### "I gave the agent the wrong API key and it ran 50 cycles for $$"

Daily budget caps total spend even in a runaway. Set
`orchestrator.daily_budget_usd` to a number you can afford to lose; default
is $25. The cycle loop refuses to dispatch once today's spend hits it.

If you discover a runaway in progress: `systemctl stop` is the kill
switch. The current cycle will finish (up to 10 minutes per the unit
file's `TimeoutStopSec`), but no new cycles dispatch.

### "The state DB is corrupted"

WAL mode makes corruption rare but not impossible (e.g. disk full mid-write).

- Back up the file: `cp state.db state.db.bak`.
- Try `sqlite3 state.db ".recover" | sqlite3 state.recovered.db` to extract.
- Worst case, delete it; the orchestrator recreates the schema on next
  start. You'll lose cycle history and the daily-spend counter resets, but
  in-flight PRs are still on GitHub — they just won't get auto-merged on
  this run (you'll have to merge them by hand).

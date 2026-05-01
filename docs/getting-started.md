# Getting Started

This walkthrough takes you from `git clone` to a working orchestrator on
your local machine in about ten minutes. We'll set up the **research**
example because it has the smallest moving-parts footprint (no GitHub auth,
no real codebase to clone, no CI to wire up) — once that's running, the
coding and marketing flavours are just swap-the-config exercises.

## 1. Prerequisites

| Thing | Version | Notes |
|---|---|---|
| Python | 3.11+ | We use `tomllib` from the stdlib |
| `git` | any modern | Only required for the coding flavour |
| Anthropic API key | — | Set as `ANTHROPIC_API_KEY` env var |
| GitHub Personal Access Token | — | Only needed if you'll use a git workspace or the git_pr shipper |

## 2. Install

```bash
git clone https://github.com/autonomousagentai/agent-orchestrator.git
cd agent-orchestrator

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate

# Minimal install (directory + noop shippers).
pip install -e .

# Or include the GitHub PR shipper (needs PyGithub).
pip install -e .[git]
```

## 3. Pick a flavour and copy the example

```bash
cp examples/research/config.toml.example  ./config.toml
cp -r examples/research/prompts            ./prompts
cp examples/research/QUESTIONS.md.example  ./workspace/QUESTIONS.md  # see below

mkdir -p workspace
cp examples/research/QUESTIONS.md.example  ./workspace/QUESTIONS.md

cp .env.example .env
$EDITOR .env                                # paste your ANTHROPIC_API_KEY
```

Open `config.toml` in an editor. Two paths to double-check:

- `paths.workspaces_dir`, `paths.logs_dir`, `paths.db_path` — these can stay
  as `./workspaces`, `./logs`, `./state.db`. They're created on first run.
- `backlog.path = "./workspace/QUESTIONS.md"` — that's the file you just
  copied above.
- `agents.prompt_file = "./prompts/cycle.md"` — that's the directory you
  copied earlier.

## 4. Smoke-test with a single cycle

```bash
agent-orchestrator --once
```

What you should see, in order:

```
INFO [agent_orchestrator] agent-orchestrator starting; backlog=./workspace/QUESTIONS.md, ...
INFO [agent_orchestrator] [cycle 1] dispatching; budget=$0.50
INFO [agent_orchestrator] [cycle 1] cwd=/.../workspace ...
... (Claude session runs)
INFO [agent_orchestrator] [cycle 1] cost=$0.0XYZ done_now=1 changed_paths=1 outcome={'slug': '...', 'outcome': 'done'}
```

The agent will edit `workspace/QUESTIONS.md` in place — flipping one
question's Status from `Ready` to `Answered` and adding `Result:` and
`Sources:` fields.

The full transcript is at `logs/cycle-1.jsonl`. Tail it with `jq` if you
want to see every tool call:

```bash
cat logs/cycle-1.jsonl | jq .
```

## 5. Inspect what happened

```bash
agent-orchestrator-status
```

You'll see a JSON dump of the cycle row plus today's spend.

## 6. Run the loop

When the smoke test looks right, run continuously:

```bash
agent-orchestrator
```

The orchestrator will keep cycling until:

- The backlog has no more `Ready` tasks (it'll start hitting `no_progress`
  and pause after `orchestrator.no_progress_pause_after` consecutive
  no-progress cycles, default 3).
- You hit the daily budget (`orchestrator.daily_budget_usd`).
- You Ctrl-C / SIGTERM it — the in-flight cycle finishes, then it exits.

To watch live:

```bash
agent-orchestrator-status --watch        # in a second terminal
tail -f logs/orchestrator.log            # in a third
```

## 7. Switch to a different flavour

Now that the loop works end-to-end, try the coding example to see the
GitHub-PR shipper:

```bash
# From a clean state:
rm -rf logs workspaces state.db prompts config.toml

cp examples/coding/config.toml.example  ./config.toml
cp -r examples/coding/prompts           ./prompts
$EDITOR config.toml                     # set workspace.git.repo and base_branch
$EDITOR .env                            # add GITHUB_TOKEN

agent-orchestrator --once
```

For the coding example, `workspaces/repo/` will get cloned on first run,
and the orchestrator will look for `BACKLOG.md` inside it. You'll need a
real backlog file in the target repo (see `examples/coding/BACKLOG.md.example`
for the shape).

## 8. Common first-run gotchas

**"backlog missing or empty; nothing to do"**
The path in `[backlog].path` is wrong, or the file has no `### slug:`
headings yet. Check by running:
```python
python -c "from agent_orchestrator.backlog.markdown import MarkdownBacklog; \
            print(MarkdownBacklog('./workspace/QUESTIONS.md').snapshot())"
```

**"GITHUB_TOKEN env var is required"**
Either you set `workspace.type = git` / `shipper.type = git_pr` without
exporting the token, or your `.env` isn't being read. The CLI does NOT
auto-load `.env` — export the vars in your shell first:
```bash
set -a; source .env; set +a
agent-orchestrator
```
(systemd handles the .env automatically via `EnvironmentFile=`.)

**Rate-limit / 429 from the Claude API**
You're cycling too fast. Raise `orchestrator.poll_interval_seconds` or
lower `orchestrator.cycle_budget_usd`.

**The agent keeps producing no_progress**
Open `logs/cycle-N.jsonl` for the most recent cycle and look at the final
assistant message. Usually the prompt is asking for something the agent
can't satisfy with the tools/permissions you've granted — or the backlog
has no `Ready` tasks. Bump `LOG_LEVEL=DEBUG` for more detail.

## What's next

- Read [configuration.md](configuration.md) for the full config schema.
- Read [extending.md](extending.md) to plug in a custom backlog source,
  workspace, or shipper.
- Read [operations.md](operations.md) to deploy this as a long-running
  service on a VPS.

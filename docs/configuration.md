# Configuration Reference

The orchestrator reads a single `config.toml` file (path can be overridden
with `-c <path>` or the `ORCHESTRATOR_CONFIG` env var). Two env vars
provide secrets and never live in the config:

| Env var | Required when | Used for |
|---|---|---|
| `ANTHROPIC_API_KEY` | always | Authenticating Claude SDK calls |
| `GITHUB_TOKEN` | `workspace.type = git` or `shipper.type = git_pr` | Cloning, pushing, opening PRs |
| `LOG_LEVEL` | optional | Default `INFO`. `DEBUG` shows shell commands and full hook payloads. |
| `ORCHESTRATOR_CONFIG` | optional | Override config path. Defaults to `./config.toml`. |

All paths in the config are resolved relative to the directory the config
file lives in. Absolute paths are passed through unchanged.

---

## `[orchestrator]`

Global loop and budget knobs.

```toml
[orchestrator]
poll_interval_seconds   = 60     # delay between cycles (after each cycle, on idle, after budget hit)
watch_interval_seconds  = 30     # how often shipper.watch() is called
no_progress_pause_after = 3      # after N back-to-back no_progress cycles, pause until restart
daily_budget_usd        = 25.0   # per-UTC-day spend cap; cycle loop pauses when hit
cycle_budget_usd        = 2.0    # per-cycle cap (also clamped down by remaining daily)
```

| Field | Default | Notes |
|---|---|---|
| `poll_interval_seconds` | 60 | Sleep between iterations of the cycle loop. Don't drop below 30 unless you know what you're doing — Anthropic rate limits will bite. |
| `watch_interval_seconds` | 30 | How often `shipper.watch()` runs. For `GitPRShipper` this is the GitHub CI polling interval. |
| `no_progress_pause_after` | 3 | After this many cycles with no shippable output, the loop pauses until restart. Prevents burning tokens on a stuck/empty backlog. |
| `daily_budget_usd` | 25.0 | UTC-day spend cap. The cycle loop sleeps when reached; the watch loop keeps running. |
| `cycle_budget_usd` | 2.0 | Per-cycle cap. The actual budget passed to the SDK is `min(cycle_budget, daily_budget - today_spend)`. |

---

## `[paths]`

Where the orchestrator stores its runtime artifacts.

```toml
[paths]
workspaces_dir = "./workspaces"
logs_dir       = "./logs"
db_path        = "./state.db"
```

| Field | Used for |
|---|---|
| `workspaces_dir` | Parent of working directories. `GitWorkspace` clones into `<workspaces_dir>/repo/`; `DirectoryWorkspace` puts its files where you tell it via `workspace.directory.path` (which can but doesn't have to live here). |
| `logs_dir` | The orchestrator's `orchestrator.log` and per-cycle `cycle-N.jsonl` transcripts. |
| `db_path` | SQLite file storing cycles + daily spend. Used to rebuild in-flight state on restart. |

All three are created on first run if they don't exist.

---

## `[agents]`

What the Claude session looks like each cycle.

```toml
[agents]
model           = "claude-sonnet-4-6"
max_turns       = 200
permission_mode = "acceptEdits"
setting_sources = ["project"]                # optional
prompt_file     = "./prompts/cycle.md"        # OR prompt_template = "..."
system_prompt_file = ""                       # optional, defaults to none
allowed_tools = [                             # optional override
  "Bash", "Read", "Write", "Edit", "MultiEdit",
  "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite", "Task",
]
cycle_done_marker = "CYCLE_DONE"              # optional override
```

| Field | Default | Notes |
|---|---|---|
| `model` | (required) | Any model the SDK accepts: `claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5`. Haiku is plenty for triage / simple tasks; sonnet is the sweet spot for most work. |
| `max_turns` | 200 | Hard cap on agent loop length per cycle. The `cycle_budget_usd` usually bites first. |
| `permission_mode` | `acceptEdits` | Passes through to the SDK. `acceptEdits` lets the agent run without prompting; tighten to `default` if you want it to stop and ask. |
| `setting_sources` | `[]` | If `["project"]`, the SDK reads the workspace's `.claude/` directory — useful for the coding flavour where you have project-defined subagents. Leave empty for a clean slate. |
| `prompt_file` | (required if no `prompt_template`) | Path to the cycle prompt template. See [Prompt placeholders](#prompt-placeholders) below. |
| `prompt_template` | (alternative to `prompt_file`) | Inline template. Mostly useful for tests. |
| `system_prompt_file` | none | Optional system-prompt override. By default, no system prompt is set and the agent runs with the SDK default. |
| `allowed_tools` | (built-in default list) | Restrict or expand the tool set. Add MCP tool names here too. |
| `cycle_done_marker` | `CYCLE_DONE` | The keyword the orchestrator looks for in the agent's final message to parse the outcome line. |

### Prompt placeholders

The orchestrator passes these `{...}` substitutions into your template
before sending it to the agent:

| Placeholder | Value |
|---|---|
| `{cycle_id}` | Integer, monotonically incrementing |
| `{workspace}` | Absolute path to the workspace dir (= the agent's cwd) |
| `{backlog_path}` | Absolute path to the backlog file *if* it lives inside the workspace; empty string otherwise |
| `{skip_slugs}` | Comma-separated list of slugs the agent should not pick (because the shipper has them in flight); empty string if none |

### The `CYCLE_DONE` handshake

The prompt should ask the agent, as its final output, to print one line:

```
CYCLE_DONE slug=<task-slug> outcome=<done|sent-back|no-task|halted>
```

The orchestrator parses this to know which task to ship. If the line is
missing, the orchestrator falls back to "any task whose status flipped to
done in the backlog diff" — but the explicit handshake is more reliable
when you have multiple tasks moving at once.

---

## `[backlog]`

Where tasks come from.

```toml
[backlog]
type             = "markdown"
path             = "./workspace/BACKLOG.md"
status_field     = "Status"           # name of the field that carries status
done_values      = ["Done"]           # values that count as terminal
acceptance_field = "Acceptance"       # field whose value is a bullet list
```

| Field | Default | Notes |
|---|---|---|
| `type` | `markdown` | Currently the only built-in. See [extending.md](extending.md) to add others. |
| `path` | (required) | The backlog file. Usually inside the workspace so the agent can read+edit it. |
| `status_field` | `Status` | Case-insensitive match on the field key. Works with `State`, `Stage`, `Phase`, etc. |
| `done_values` | `["Done"]` | Multiple values are OR'd: `["Published", "Done"]` treats either as terminal. Compared case-insensitively. |
| `acceptance_field` | `Acceptance` | Bullets immediately under this field are collected as the task's acceptance criteria. |

### Backlog schema (markdown)

```markdown
### task-slug: Free-text title

- **Status:** Ready
- **Priority:** P1
- **Owner:** developer
- **Depends-On:** other-slug
- **Acceptance:**
  - first criterion
  - second criterion
- **Result:** (filled in by the agent when complete)
```

Block boundaries: a task block starts at `### slug:` and ends at the next
`### `, `## `, or `---`. Field values are stripped of surrounding `**` /
`` ` `` for clean comparisons.

The slug must be filesystem-safe: lowercase letters, digits, dashes, dots,
underscores. The orchestrator uses it for branch names, deliverable
directories, etc.

---

## `[workspace]` and `[workspace.<type>]`

Where the agent works.

### Git workspace

```toml
[workspace]
type = "git"

[workspace.git]
repo            = "your-org/your-repo"
base_branch     = "main"
git_user_name   = "agent-orchestrator"
git_user_email  = "agent@noreply.local"
autofix_command = ["ruff", "check", "--fix", "."]   # optional, run before commit
```

A single persistent clone lives under `<workspaces_dir>/repo/`. Between
cycles the orchestrator does:

1. `git fetch origin --prune`
2. `git checkout <base_branch>`
3. `git reset --hard origin/<base_branch>`
4. `git clean -fd` (preserving `.venv`, `node_modules`, `.pytest_cache`,
   `.ruff_cache`, `.mypy_cache`)
5. Delete any leftover `cycle-*` branches
6. `git checkout -b cycle-<id>`

After the agent finishes, the cycle branch is renamed to
`<feature_prefix><slug>` (default `feature/<slug>`) by `GitPRShipper`,
optionally autofixed, then committed and pushed.

### Directory workspace

```toml
[workspace]
type = "directory"

[workspace.directory]
path            = "./workspace"
wipe_subdirs    = ["scratch"]                              # wiped at each cycle start
ignore_patterns = [".git", "__pycache__", ".DS_Store"]    # excluded from the diff
```

A plain directory. At the start of each cycle, the orchestrator snapshots
`(file_path → mtime, size)` of every file under `path` (minus
`ignore_patterns`). At the end, it diffs against the snapshot to find the
files the agent created or modified.

`wipe_subdirs` is for transient scratch dirs you don't want to accumulate
across cycles. They're recreated empty at each cycle start.

---

## `[shipper]` and `[shipper.<type>]`

What happens with the cycle's output.

### `git_pr`: open + auto-merge a GitHub PR

```toml
[shipper]
type = "git_pr"

[shipper.git_pr]
feature_prefix    = "feature/"
required_workflow = "ci"            # if set, only this workflow's pass counts as green
opened_label      = "agent-pr"      # cosmetic; "" to skip
merged_label      = "agent-merged"  # cosmetic
escalate_label    = "needs-human"   # applied when CI fails
auto_squash_merge = true
commit_message_template = """\
{prefix}: {title}

Auto-shipped by agent-orchestrator from backlog task `{slug}`.
"""
```

This shipper requires `[workspace.git]` to be configured (it needs to know
which repo to push to). The flow:

1. Rename `cycle-<id>` → `<feature_prefix><slug>`
2. Run `autofix_command` if set
3. Commit, force-push (with `--force-with-lease`), open PR
4. Apply `opened_label`
5. **Watch loop** polls the PR's CI status:
   - `success` and `auto_squash_merge = true` → squash-merge, apply
     `merged_label`
   - `failure` → apply `escalate_label`, leave a comment, leave the PR
     open for human handling
   - `pending` / `unknown` → keep waiting

If `required_workflow` is set, only that workflow's status is checked
(other check runs are advisory). Otherwise all check runs must succeed.

### `directory`: copy outputs into a deliverables tree

```toml
[shipper]
type = "directory"

[shipper.directory]
deliverables_dir           = "./deliverables"
version_existing           = true     # if "<slug>/" exists, write to "<slug>-v2/", "-v3/"
reset_workspace_after_ship = true     # workspace.reset_after_no_progress() after ship
```

For non-code work. After a cycle completes:

1. Compute the changed file list from the workspace.
2. Copy each changed file into `<deliverables_dir>/<slug>/`,
   preserving the workspace's relative structure.
3. Drop a `_TASK.md` file at the top of that directory containing the
   task's title, fields, and acceptance criteria.
4. Optionally reset the workspace so the next cycle starts clean.

### `noop`: just record the cycle

```toml
[shipper]
type = "noop"
```

Use when the backlog itself is the deliverable (e.g. a research backlog
where the agent answers questions in place). The orchestrator records a
`shipped` cycle row, but does nothing else.

---

## Putting it together

The minimal valid config (research example):

```toml
[orchestrator]
daily_budget_usd = 5.0
cycle_budget_usd = 0.5

[paths]
workspaces_dir = "./workspaces"
logs_dir       = "./logs"
db_path        = "./state.db"

[agents]
model       = "claude-haiku-4-5"
max_turns   = 80
prompt_file = "./prompts/cycle.md"

[backlog]
type        = "markdown"
path        = "./workspace/QUESTIONS.md"
done_values = ["Answered"]

[workspace]
type = "directory"

[workspace.directory]
path = "./workspace"

[shipper]
type = "noop"
```

That's about 25 lines. Everything else is opt-in.

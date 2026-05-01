# Extending agent-orchestrator

The orchestrator's main loop only talks to three abstract interfaces:
`BacklogProvider`, `Workspace`, and `Shipper`. Adding a new task source,
working environment, or delivery target is a matter of writing a class for
the interface you care about and registering it in `factory.py`.

This doc walks through writing each kind of plug-in with concrete examples.

---

## 1. Custom backlog source

Use case: pull tasks from Linear, GitHub Issues, a Notion database, a
spreadsheet, etc. — anything where the markdown-file model doesn't fit.

### The interface

```python
# agent_orchestrator/backlog/base.py
class BacklogProvider(ABC):
    @abstractmethod
    def snapshot(self) -> list[Task]:
        ...

    @abstractmethod
    def is_done(self, task: Task) -> bool:
        ...

    # Default implementations you usually don't need to override:
    def diff_done(self, before: list[Task], after: list[Task]) -> list[Task]: ...
    def find(self, tasks: list[Task], slug: str) -> Optional[Task]: ...
    def render_summary(self, task: Task) -> str: ...
```

A `Task` has `slug`, `title`, `fields: dict[str, str]`, `acceptance:
list[str]`, and `raw_block: str`. The slug must be filesystem-safe (the
shipper uses it for branch names and deliverable directories).

### Example: GitHub Issues backlog

```python
# my_extensions/backlog_github_issues.py
from agent_orchestrator.backlog import BacklogProvider, Task

class GitHubIssuesBacklog(BacklogProvider):
    def __init__(self, token: str, repo: str, ready_label: str = "ready",
                 done_label: str = "done") -> None:
        from github import Github
        self.gh = Github(token).get_repo(repo)
        self.ready_label = ready_label
        self.done_label = done_label

    def snapshot(self) -> list[Task]:
        out: list[Task] = []
        for issue in self.gh.get_issues(state="all", labels=[self.ready_label]):
            slug = self._slug(issue)
            labels = {l.name for l in issue.labels}
            t = Task(
                slug=slug,
                title=issue.title,
                fields={
                    "Issue": str(issue.number),
                    "Status": "Done" if self.done_label in labels else "Ready",
                    "Priority": self._priority(labels),
                    "Body": issue.body or "",
                },
                acceptance=self._acceptance_from_body(issue.body or ""),
            )
            out.append(t)
        return out

    def is_done(self, task: Task) -> bool:
        return task.fields.get("Status") == "Done"

    def _slug(self, issue) -> str:
        # 'gh-123-rate-limit-middleware' is filesystem-safe
        slugified = "-".join(issue.title.lower().split())[:50]
        return f"gh-{issue.number}-{slugified}"

    # ... helpers omitted ...
```

### Wiring it up

Register your provider in `factory._build_backlog`:

```python
def _build_backlog(cfg: Config) -> BacklogProvider:
    btype = cfg.backlog.type
    if btype == "markdown":
        return MarkdownBacklog(...)
    if btype == "github_issues":
        from my_extensions.backlog_github_issues import GitHubIssuesBacklog
        return GitHubIssuesBacklog(
            token=cfg.gh_token,
            repo=cfg.workspace.git["repo"],
            ready_label=cfg.backlog._extra.get("ready_label", "ready"),
            done_label=cfg.backlog._extra.get("done_label", "done"),
        )
    raise SystemExit(f"unknown backlog type: {btype!r}")
```

(For the config to carry custom fields, you'll want to extend
`BacklogSection` in `config.py` or stash them in a free-form `_extra` dict.
The simplest path is to subclass `Config` in your fork.)

### Caveat: the agent has to be able to see the backlog

The agent reads the backlog from inside its workspace. If the backlog is
external (Linear API, etc.), you have two options:

1. **Hydrate it into the workspace at the start of each cycle**: write a
   `backlog.md` derived from the API into `<workspace>/backlog.md` so the
   agent reads a snapshot. Then your `is_done()` reads from the API, not
   the file. This is the easiest approach.
2. **Give the agent API tools** (e.g. an MCP server for Linear) so it can
   query and update the source directly. Then your `BacklogProvider` only
   needs to compute snapshots — the agent does the writes.

---

## 2. Custom workspace

Use case: you want the agent to operate inside a Docker container, on a
remote SSH host, in an ephemeral cloud sandbox, etc.

### The interface

```python
# agent_orchestrator/workspaces/base.py
class Workspace(ABC):
    @property
    @abstractmethod
    def path(self) -> Path: ...        # cwd for the Claude session

    @abstractmethod
    def prepare(self, cycle_id: int) -> None: ...

    @abstractmethod
    def changed_paths(self) -> list[str]: ...

    @abstractmethod
    def reset_after_no_progress(self) -> None: ...
```

The orchestrator only needs four things from a workspace:
- a directory it can pass to the SDK as `cwd`
- a way to "start fresh" before each cycle
- a way to find out what files changed during a cycle
- a way to roll back a cycle that didn't ship

### Example: Docker workspace

```python
# my_extensions/workspace_docker.py
import subprocess
from pathlib import Path
from agent_orchestrator.workspaces import Workspace

class DockerWorkspace(Workspace):
    """Runs each cycle inside a fresh container; copies the result back to
    a host bind-mount when the cycle finishes."""

    def __init__(self, image: str, host_dir: Path, container_workdir: str = "/work"):
        self.image = image
        self.host_dir = host_dir
        self.host_dir.mkdir(parents=True, exist_ok=True)
        self.container_workdir = container_workdir
        self._container_id: str | None = None

    @property
    def path(self) -> Path:
        # The SDK runs locally — we point cwd at the host bind-mount and
        # rely on the agent's tools running on the host. (For a true
        # sandboxed-in-container model you'd need to wrap the SDK itself.)
        return self.host_dir

    def prepare(self, cycle_id: int) -> None:
        # Wipe the bind-mount.
        for child in self.host_dir.iterdir():
            ...
        # Spin up the container so it's ready when the agent runs commands.
        self._container_id = subprocess.check_output([
            "docker", "run", "-d", "--rm",
            "-v", f"{self.host_dir}:{self.container_workdir}",
            self.image, "sleep", "infinity",
        ], text=True).strip()

    def changed_paths(self) -> list[str]:
        # Diff bind-mount against an initial snapshot...
        ...

    def reset_after_no_progress(self) -> None:
        if self._container_id:
            subprocess.run(["docker", "kill", self._container_id])
            self._container_id = None
```

### Wiring it up

Register in `factory._build_workspace`. Most non-trivial workspaces will
also want their own `[workspace.<your-type>]` section in config.py.

### Caveat: hooks and base-branch protection

The orchestrator's default `bash_guard` only protects a base branch when
it's a `GitWorkspace`. If your custom workspace is git-backed too, you can
expose a `cfg.base_branch` attribute and tweak `Orchestrator._build_hooks`
to pick it up.

---

## 3. Custom shipper

Use case: deliver outputs somewhere other than a GitHub PR or a local
directory — Slack, Trello, S3, an SFTP drop, a CMS, etc.

### The interface

```python
# agent_orchestrator/shippers/base.py
class Shipper(ABC):
    @abstractmethod
    def ship(
        self,
        *,
        task: Task,
        workspace: Workspace,
        cycle_id: int,
        changed_paths: list[str],
        cycle_notes: str = "",
    ) -> ShipResult:
        ...

    def watch(self) -> list[tuple[int, ShipResult]]:
        # Override if your shipper has async finalization (e.g. polling CI).
        return []

    def in_flight_slugs(self) -> list[str]:
        # Override to give the cycle runner a skip-list (slugs already in flight).
        return []
```

`ShipResult.status` is one of `"shipped"`, `"in_flight"`, `"no_progress"`,
`"error"`. `in_flight` means "handed off to an external system, watch()
will finish it later". The orchestrator persists the cycle row with that
status and `watch()` updates it when the external system reports back.

### Example: Slack shipper

```python
# my_extensions/shipper_slack.py
from pathlib import Path
from agent_orchestrator.backlog import BacklogProvider, Task
from agent_orchestrator.shippers import ShipResult, Shipper
from agent_orchestrator.workspaces import Workspace

class SlackShipper(Shipper):
    """Posts the cycle's primary deliverable file as a Slack message."""

    def __init__(
        self,
        backlog: BacklogProvider,
        webhook_url: str,
        primary_filename: str = "post.md",
    ) -> None:
        self.backlog = backlog
        self.webhook_url = webhook_url
        self.primary_filename = primary_filename

    def ship(self, *, task, workspace, cycle_id, changed_paths, cycle_notes=""):
        primary = workspace.path / self.primary_filename
        if not primary.exists():
            return ShipResult(
                status="error",
                task_slug=task.slug,
                error=f"expected {self.primary_filename} in workspace, not found",
            )
        body = primary.read_text(encoding="utf-8")
        title = task.title
        text = f"*{title}* (`{task.slug}`)\n\n{body[:2900]}"
        import requests
        r = requests.post(self.webhook_url, json={"text": text}, timeout=15)
        r.raise_for_status()
        return ShipResult(
            status="shipped",
            task_slug=task.slug,
            target_path=str(primary),
            notes=f"posted to slack ({r.status_code})",
        )
```

### Wiring it up

```python
# factory._build_shipper
if stype == "slack":
    sl = cfg.shipper._extra
    return SlackShipper(
        backlog=backlog,
        webhook_url=os.environ["SLACK_WEBHOOK_URL"],
        primary_filename=sl.get("primary_filename", "post.md"),
    )
```

### Patterns for in-flight shippers

If your shipper hands work off to an external system that takes time to
finalize (CI, a moderation queue, an approval workflow), follow
`GitPRShipper`'s pattern:

1. `ship()` returns `status="in_flight"` with whatever identifiers the
   external system gave back (PR number, queue ticket, etc.). Stash them
   in `ShipResult.pr_number` / `target_path` / `notes`.
2. Track them in an internal list (`self._in_flight`).
3. `watch()` polls each in-flight item, returns `(cycle_id, ShipResult)`
   pairs for the ones whose status changed.
4. Implement `restore_in_flight(items)` so the orchestrator can re-seed
   your watcher after a process restart. The orchestrator passes
   `(cycle_id, pr_number, branch, task_slug)` tuples drawn from the
   `cycles` table.

The orchestrator's main loop calls `watch()` every
`orchestrator.watch_interval_seconds` and persists every result via
`State.update_cycle(cycle_id, ...)`.

---

## 4. Custom prompt template

This isn't a code change — just a different `prompts/cycle.md`. The
template is yours to write. Useful patterns:

### Multi-task per cycle

The default prompt asks the agent to halt after one task. To process more,
loosen the halt condition:

> Continue until you've completed up to 3 Ready tasks, OR after 12 internal
> cycles without progress, OR when no Ready task is dispatchable. After
> each task, output `CYCLE_DONE_PARTIAL slug=<slug>`. After the final task,
> output `CYCLE_DONE slug=<final-slug> outcome=done`.

You'd then write a custom outcome-parser that aggregates the partial lines.

### Pre-flight inspection

> Before doing any work, check that today's date is not a Saturday or
> Sunday (use the system clock). If it is, output
> `CYCLE_DONE slug=- outcome=no-task` and halt without any other action.

### Specialist routing

For projects with multiple specialist subagents in `.claude/agents/`:

> Use the Task tool to invoke the right specialist for the task's Owner
> field: `developer` -> developer, `qa` -> qa, etc. Pass them a
> self-contained brief that includes the task title, acceptance criteria,
> and any Files-Allowed / Files-Forbidden constraints. They cannot see
> this conversation.

The coding flavour's prompt is a worked example of this pattern.

---

## 5. Testing your extensions

Each interface is tiny — a couple of methods — so the smart move is to
write a unit test that exercises just your class without the orchestrator
loop in the way:

```python
# tests/test_my_extensions.py
def test_github_issues_backlog_snapshot(monkeypatch):
    from my_extensions.backlog_github_issues import GitHubIssuesBacklog
    fake_repo = ...   # mock with PyGithub stubs
    backlog = GitHubIssuesBacklog(token="fake", repo="x/y")
    backlog.gh = fake_repo
    tasks = backlog.snapshot()
    assert tasks[0].slug == "gh-42-fix-thing"
    assert backlog.is_done(tasks[0]) is False
```

Once the unit test passes, run a real cycle with `agent-orchestrator
--once` and check `logs/cycle-1.jsonl` to confirm the integration works
end-to-end. The orchestrator's `--once` mode is your friend here.

---

## 6. When to fork vs. PR upstream

Fork-and-add patterns:

- A backlog provider for an internal-only system (your company's ticket
  tracker, a custom Notion schema, etc.).
- A shipper that talks to a private service.
- A workspace that depends on an internal CLI tool.

PR-upstream patterns:

- A backlog provider for a public API (Linear, Jira, GitHub Issues, Notion).
- A shipper for a common destination (S3, Confluence, Mastodon).
- Bug fixes or correctness improvements in the core loop.
- New built-in placeholders or hook points.

The interfaces are deliberately minimal. If you find yourself needing more
shape from the orchestrator (e.g. "I need access to the Claude SDK
client mid-shipping"), open an issue first — that usually means we should
extend the interface rather than push you to subclass `Orchestrator`.

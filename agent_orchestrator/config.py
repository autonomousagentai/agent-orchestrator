"""TOML config loader.

Top-level shape:

    [orchestrator]
    poll_interval_seconds        = 60
    watch_interval_seconds       = 30
    no_progress_pause_after      = 3
    daily_budget_usd             = 25.0
    cycle_budget_usd             = 2.0

    [paths]
    workspaces_dir = "./workspaces"
    logs_dir       = "./logs"
    db_path        = "./state.db"

    [agents]
    model           = "claude-sonnet-4-6"
    max_turns       = 200
    permission_mode = "acceptEdits"
    setting_sources = ["project"]    # optional
    system_prompt_file = ""           # optional path
    allowed_tools   = ["Bash", "Read", "Write", ...]   # optional override
    prompt_file     = "./prompts/cycle.md"

    [backlog]
    type             = "markdown"
    path             = "./BACKLOG.md"      # path relative to workspace, or abs
    status_field     = "Status"
    done_values      = ["Done"]
    acceptance_field = "Acceptance"

    [workspace]
    type = "git"   # or "directory"

    [workspace.git]
    repo            = "owner/name"
    base_branch     = "main"
    git_user_name   = "agent-orchestrator"
    git_user_email  = "agent@noreply.local"
    autofix_command = []   # e.g. ["ruff", "check", "--fix", "."]

    [workspace.directory]
    path          = "./workspace"
    wipe_subdirs  = ["scratch"]
    ignore_patterns = [".git", "__pycache__"]

    [shipper]
    type = "git_pr"   # or "directory" or "noop"

    [shipper.git_pr]
    feature_prefix    = "feature/"
    required_workflow = "ci"
    opened_label      = "agent-pr"
    merged_label      = "agent-merged"
    escalate_label    = "needs-human"
    auto_squash_merge = true

    [shipper.directory]
    deliverables_dir              = "./deliverables"
    version_existing              = true
    reset_workspace_after_ship    = true
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore


@dataclass
class OrchestratorSection:
    poll_interval_seconds: int = 60
    watch_interval_seconds: int = 30
    no_progress_pause_after: int = 3
    daily_budget_usd: float = 25.0
    cycle_budget_usd: float = 2.0


@dataclass
class PathsSection:
    workspaces_dir: Path
    logs_dir: Path
    db_path: Path


@dataclass
class AgentsSection:
    model: str
    max_turns: int
    prompt_template: str
    allowed_tools: list[str]
    permission_mode: str = "acceptEdits"
    setting_sources: list[str] = field(default_factory=list)
    system_prompt: Optional[str] = None
    cycle_done_marker: str = "CYCLE_DONE"


@dataclass
class BacklogSection:
    type: str
    path: Path
    status_field: str = "Status"
    done_values: list[str] = field(default_factory=lambda: ["Done"])
    acceptance_field: str = "Acceptance"


@dataclass
class WorkspaceSection:
    type: str  # "git" or "directory"
    git: dict[str, Any] = field(default_factory=dict)
    directory: dict[str, Any] = field(default_factory=dict)


@dataclass
class ShipperSection:
    type: str  # "git_pr", "directory", "noop"
    git_pr: dict[str, Any] = field(default_factory=dict)
    directory: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    orchestrator: OrchestratorSection
    paths: PathsSection
    agents: AgentsSection
    backlog: BacklogSection
    workspace: WorkspaceSection
    shipper: ShipperSection
    anthropic_api_key: str
    gh_token: str
    root_dir: Path

    # ------------------------------------------------------------------ load

    @classmethod
    def load(cls, path: str | Path = "config.toml") -> "Config":
        cfg_path = Path(path).resolve()
        if not cfg_path.exists():
            raise SystemExit(
                f"config not found at {cfg_path}. "
                "Copy one of the examples/ configs to ./config.toml and edit."
            )
        with cfg_path.open("rb") as f:
            data = tomllib.load(f)

        root = cfg_path.parent

        def _path(rel: str) -> Path:
            p = Path(rel)
            return p if p.is_absolute() else (root / p).resolve()

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        gh_token = os.environ.get("GITHUB_TOKEN", "")
        if not api_key:
            raise SystemExit("ANTHROPIC_API_KEY env var is required")

        # ----- [orchestrator]
        o = data.get("orchestrator", {})
        orch = OrchestratorSection(
            poll_interval_seconds=int(o.get("poll_interval_seconds", 60)),
            watch_interval_seconds=int(o.get("watch_interval_seconds", 30)),
            no_progress_pause_after=int(o.get("no_progress_pause_after", 3)),
            daily_budget_usd=float(o.get("daily_budget_usd", 25.0)),
            cycle_budget_usd=float(o.get("cycle_budget_usd", 2.0)),
        )

        # ----- [paths]
        p = data["paths"]
        paths = PathsSection(
            workspaces_dir=_path(p["workspaces_dir"]),
            logs_dir=_path(p["logs_dir"]),
            db_path=_path(p["db_path"]),
        )

        # ----- [agents]
        a = data["agents"]
        prompt_file = a.get("prompt_file")
        if prompt_file:
            prompt_template = _path(prompt_file).read_text(encoding="utf-8")
        elif "prompt_template" in a:
            prompt_template = a["prompt_template"]
        else:
            raise SystemExit("[agents] needs prompt_file or prompt_template")
        sys_prompt = None
        if a.get("system_prompt_file"):
            sys_prompt = _path(a["system_prompt_file"]).read_text(encoding="utf-8")
        elif a.get("system_prompt"):
            sys_prompt = a["system_prompt"]
        from .cycle import DEFAULT_ALLOWED_TOOLS

        agents = AgentsSection(
            model=a["model"],
            max_turns=int(a.get("max_turns", 200)),
            prompt_template=prompt_template,
            allowed_tools=list(a.get("allowed_tools", DEFAULT_ALLOWED_TOOLS)),
            permission_mode=a.get("permission_mode", "acceptEdits"),
            setting_sources=list(a.get("setting_sources", [])),
            system_prompt=sys_prompt,
            cycle_done_marker=a.get("cycle_done_marker", "CYCLE_DONE"),
        )

        # ----- [backlog]
        b = data["backlog"]
        backlog = BacklogSection(
            type=b.get("type", "markdown"),
            path=_path(b["path"]),
            status_field=b.get("status_field", "Status"),
            done_values=list(b.get("done_values", ["Done"])),
            acceptance_field=b.get("acceptance_field", "Acceptance"),
        )

        # ----- [workspace]
        ws = data["workspace"]
        workspace = WorkspaceSection(
            type=ws["type"],
            git=dict(ws.get("git", {})),
            directory=dict(ws.get("directory", {})),
        )
        if workspace.type == "git" and not gh_token:
            raise SystemExit(
                "workspace.type = 'git' requires GITHUB_TOKEN env var (used for clone + push)"
            )

        # ----- [shipper]
        sh = data["shipper"]
        shipper = ShipperSection(
            type=sh["type"],
            git_pr=dict(sh.get("git_pr", {})),
            directory=dict(sh.get("directory", {})),
        )
        if shipper.type == "git_pr" and not gh_token:
            raise SystemExit(
                "shipper.type = 'git_pr' requires GITHUB_TOKEN env var"
            )

        return cls(
            orchestrator=orch,
            paths=paths,
            agents=agents,
            backlog=backlog,
            workspace=workspace,
            shipper=shipper,
            anthropic_api_key=api_key,
            gh_token=gh_token,
            root_dir=root,
        )

    def ensure_dirs(self) -> None:
        for p in (self.paths.workspaces_dir, self.paths.logs_dir):
            p.mkdir(parents=True, exist_ok=True)
        self.paths.db_path.parent.mkdir(parents=True, exist_ok=True)

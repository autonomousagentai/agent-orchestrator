"""Build runtime objects from a Config.

Kept separate from `Config` so the config dataclass stays a plain data
container. The orchestrator main module imports `build_runtime()` to
materialize the chosen backlog provider, workspace, and shipper.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .backlog import BacklogProvider, MarkdownBacklog
from .config import Config
from .shippers import DirectoryShipper, NoopShipper, Shipper
from .workspaces import DirectoryWorkspace, GitWorkspace, Workspace


@dataclass
class Runtime:
    backlog: BacklogProvider
    workspace: Workspace
    shipper: Shipper
    backlog_path_inside_workspace: Optional[Path]


def build_runtime(cfg: Config) -> Runtime:
    backlog = _build_backlog(cfg)
    workspace = _build_workspace(cfg)
    shipper = _build_shipper(cfg, backlog)

    backlog_path_inside_workspace = _resolve_backlog_in_workspace(
        backlog_path=cfg.backlog.path, workspace_dir=workspace.path
    )
    return Runtime(
        backlog=backlog,
        workspace=workspace,
        shipper=shipper,
        backlog_path_inside_workspace=backlog_path_inside_workspace,
    )


def _build_backlog(cfg: Config) -> BacklogProvider:
    btype = cfg.backlog.type
    if btype == "markdown":
        return MarkdownBacklog(
            path=cfg.backlog.path,
            status_field=cfg.backlog.status_field,
            done_values=cfg.backlog.done_values,
            acceptance_field=cfg.backlog.acceptance_field,
        )
    raise SystemExit(f"unknown backlog type: {btype!r}")


def _build_workspace(cfg: Config) -> Workspace:
    wtype = cfg.workspace.type
    if wtype == "git":
        from .workspaces.git_repo import GitWorkspaceConfig

        gw = cfg.workspace.git
        return GitWorkspace(
            GitWorkspaceConfig(
                workspaces_dir=cfg.paths.workspaces_dir,
                repo_full_name=gw["repo"],
                base_branch=gw.get("base_branch", "main"),
                gh_token=cfg.gh_token,
                git_user_name=gw.get("git_user_name", "agent-orchestrator"),
                git_user_email=gw.get("git_user_email", "agent@noreply.local"),
                autofix_command=gw.get("autofix_command") or None,
            )
        )
    if wtype == "directory":
        from .workspaces.directory import DirectoryWorkspaceConfig

        dw = cfg.workspace.directory
        ws_path = Path(dw["path"])
        if not ws_path.is_absolute():
            ws_path = (cfg.root_dir / ws_path).resolve()
        return DirectoryWorkspace(
            DirectoryWorkspaceConfig(
                workspace_dir=ws_path,
                wipe_subdirs=list(dw.get("wipe_subdirs", [])),
                ignore_patterns=list(
                    dw.get("ignore_patterns", [".git", "__pycache__", ".venv"])
                ),
            )
        )
    raise SystemExit(f"unknown workspace type: {wtype!r}")


def _build_shipper(cfg: Config, backlog: BacklogProvider) -> Shipper:
    stype = cfg.shipper.type
    if stype == "noop":
        return NoopShipper()
    if stype == "directory":
        from .shippers.directory import DirectoryShipperConfig

        ds = cfg.shipper.directory
        deliverables = Path(ds["deliverables_dir"])
        if not deliverables.is_absolute():
            deliverables = (cfg.root_dir / deliverables).resolve()
        return DirectoryShipper(
            DirectoryShipperConfig(
                deliverables_dir=deliverables,
                version_existing=bool(ds.get("version_existing", True)),
                reset_workspace_after_ship=bool(
                    ds.get("reset_workspace_after_ship", True)
                ),
            ),
            backlog,
        )
    if stype == "git_pr":
        from .shippers.git_pr import GitPRShipper, GitPRShipperConfig

        gp = cfg.shipper.git_pr
        gw = cfg.workspace.git
        if not gw:
            raise SystemExit(
                "shipper.type = 'git_pr' requires [workspace.git] (the repo is needed to ship)"
            )
        return GitPRShipper(
            GitPRShipperConfig(
                repo_full_name=gw["repo"],
                base_branch=gw.get("base_branch", "main"),
                gh_token=cfg.gh_token,
                feature_prefix=gp.get("feature_prefix", "feature/"),
                required_workflow=gp.get("required_workflow", ""),
                opened_label=gp.get("opened_label", ""),
                merged_label=gp.get("merged_label", ""),
                escalate_label=gp.get("escalate_label", ""),
                auto_squash_merge=bool(gp.get("auto_squash_merge", True)),
                commit_message_template=gp.get(
                    "commit_message_template",
                    "{prefix}: {title}\n\n"
                    "Auto-shipped by agent-orchestrator from backlog task `{slug}`.",
                ),
            ),
            backlog,
        )
    raise SystemExit(f"unknown shipper type: {stype!r}")


def _resolve_backlog_in_workspace(
    backlog_path: Path, workspace_dir: Path
) -> Optional[Path]:
    """If the backlog file lives *inside* the workspace, return the workspace-
    relative path so the agent (which sees `workspace_dir` as cwd) can read it.

    For git workspaces the file usually lives in the cloned repo; for directory
    workspaces it might live next to outputs. If the backlog path is *outside*
    the workspace, the agent won't be allowed to read it via the path-guard
    hook anyway, so we return None and the orchestrator surfaces the file
    contents in the prompt instead.
    """
    try:
        backlog_path.resolve().relative_to(workspace_dir.resolve())
        return backlog_path
    except ValueError:
        # backlog lives outside workspace — can be done but caller needs to
        # decide whether to inline it into the prompt.
        return None

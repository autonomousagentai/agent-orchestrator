"""GitHub-PR shipper.

Takes a `GitWorkspace` that's been worked in by the agent, renames the
cycle branch to `<feature_prefix><slug>`, commits + pushes it, opens a PR,
and tracks the PR's CI status. When CI is green it squash-merges; when CI
fails it leaves a label + comment for human review.

This shipper is stateful — it owns a small in-memory list of in-flight PRs
plus a hook the orchestrator main loop uses to persist them across restarts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..backlog import BacklogProvider, Task
from ..workspaces import GitWorkspace, Workspace
from .base import ShipResult, Shipper
from .github_client import GitHubClient

log = logging.getLogger(__name__)


@dataclass
class GitPRShipperConfig:
    repo_full_name: str
    base_branch: str
    gh_token: str
    feature_prefix: str = "feature/"
    required_workflow: str = ""
    opened_label: str = ""
    merged_label: str = ""
    escalate_label: str = ""
    auto_squash_merge: bool = True
    commit_message_template: str = (
        "{prefix}: {title}\n\n"
        "Auto-shipped by agent-orchestrator from backlog task `{slug}`."
    )
    # Map a task `Type` field value to a conventional-commit prefix. Falls
    # back to "chore" for unknown types.
    type_prefix_map: dict[str, str] = field(
        default_factory=lambda: {
            "implementation": "feat",
            "feature": "feat",
            "bugfix": "fix",
            "fix": "fix",
            "refactor": "refactor",
            "design": "docs",
            "docs": "docs",
            "test": "test",
            "verification": "test",
            "chore": "chore",
        }
    )


@dataclass
class _InFlight:
    cycle_id: int
    pr_number: int
    branch: str
    task_slug: str


class GitPRShipper(Shipper):
    def __init__(
        self,
        cfg: GitPRShipperConfig,
        backlog: BacklogProvider,
        *,
        # Optional persistence callbacks: called whenever an in-flight PR is
        # added or its status changes. Lets the orchestrator's State table
        # stay in sync without coupling this class to it.
        on_in_flight: Optional[Callable[[_InFlight], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.backlog = backlog
        self.gh = GitHubClient(cfg.gh_token, cfg.repo_full_name)
        self._in_flight: list[_InFlight] = []
        self._on_in_flight = on_in_flight

    # ---------------------------------------------------------- skip-list

    def in_flight_slugs(self) -> list[str]:
        """Slugs the cycle runner should *not* re-pick — already in flight."""
        try:
            return self.gh.open_feature_slugs(self.cfg.feature_prefix)
        except Exception:
            log.exception("could not fetch open PR slugs")
            return [f.task_slug for f in self._in_flight]

    # ------------------------------------------------------------ Shipper

    def ship(
        self,
        *,
        task: Task,
        workspace: Workspace,
        cycle_id: int,
        changed_paths: list[str],
        cycle_notes: str = "",
    ) -> ShipResult:
        if not isinstance(workspace, GitWorkspace):
            return ShipResult(
                status="error",
                task_slug=task.slug,
                error=f"GitPRShipper requires a GitWorkspace, got {type(workspace).__name__}",
            )
        if not changed_paths:
            return ShipResult(status="no_progress", task_slug=task.slug)

        cycle_branch = workspace.cycle_branch
        if cycle_branch is None:
            return ShipResult(
                status="error",
                task_slug=task.slug,
                error="workspace has no active cycle branch",
            )

        feature_branch = f"{self.cfg.feature_prefix}{task.slug}"
        log.info(
            "[cycle %d] shipping %s as %s (%d files changed)",
            cycle_id,
            task.slug,
            feature_branch,
            len(changed_paths),
        )

        try:
            workspace.rename_branch(cycle_branch, feature_branch)
            try:
                workspace.autofix()
            except Exception:
                log.exception("autofix failed; committing as-is")
            workspace.commit_all(self._commit_message(task))
            workspace.push(feature_branch, force_with_lease=True)
        except Exception as e:
            log.exception("[cycle %d] git ops failed", cycle_id)
            return ShipResult(
                status="error",
                task_slug=task.slug,
                branch=feature_branch,
                error=f"git ops failed: {e}",
            )

        try:
            pr = self.gh.open_pr(
                branch=feature_branch,
                base_branch=self.cfg.base_branch,
                title=task.title,
                body=self.backlog.render_summary(task),
            )
            self.gh.add_label(pr, self.cfg.opened_label)
        except Exception as e:
            log.exception("[cycle %d] PR open failed", cycle_id)
            return ShipResult(
                status="error",
                task_slug=task.slug,
                branch=feature_branch,
                error=f"open_pr failed: {e}",
            )

        in_flight = _InFlight(
            cycle_id=cycle_id, pr_number=pr.number, branch=feature_branch, task_slug=task.slug
        )
        self._in_flight.append(in_flight)
        if self._on_in_flight:
            try:
                self._on_in_flight(in_flight)
            except Exception:
                log.exception("on_in_flight callback failed (non-fatal)")

        return ShipResult(
            status="in_flight",
            task_slug=task.slug,
            branch=feature_branch,
            pr_number=pr.number,
            notes=f"PR #{pr.number} opened; CI pending",
        )

    def watch(self) -> list[tuple[int, ShipResult]]:
        out: list[tuple[int, ShipResult]] = []
        still_open: list[_InFlight] = []
        for f in self._in_flight:
            try:
                pr = self.gh.get_pr(f.pr_number)
            except Exception:
                log.exception("could not fetch PR #%d", f.pr_number)
                still_open.append(f)
                continue
            if pr.merged:
                self.gh.add_label(pr, self.cfg.merged_label)
                out.append(
                    (
                        f.cycle_id,
                        ShipResult(
                            status="shipped",
                            task_slug=f.task_slug,
                            branch=f.branch,
                            pr_number=f.pr_number,
                            notes="merged",
                        ),
                    )
                )
                continue
            if pr.state == "closed":
                out.append(
                    (
                        f.cycle_id,
                        ShipResult(
                            status="error",
                            task_slug=f.task_slug,
                            branch=f.branch,
                            pr_number=f.pr_number,
                            notes="closed without merge",
                            error="closed without merge",
                        ),
                    )
                )
                continue
            status = self.gh.ci_status(pr, self.cfg.required_workflow or None)
            log.info("PR #%d ci=%s checks=%s", f.pr_number, status.overall, status.checks)
            if status.overall == "success" and self.cfg.auto_squash_merge:
                ok = self.gh.squash_merge(pr, commit_title=pr.title)
                if ok:
                    self.gh.add_label(pr, self.cfg.merged_label)
                    out.append(
                        (
                            f.cycle_id,
                            ShipResult(
                                status="shipped",
                                task_slug=f.task_slug,
                                branch=f.branch,
                                pr_number=f.pr_number,
                                notes="squash-merged after CI success",
                            ),
                        )
                    )
                    continue
                else:
                    # Mergeable race — leave it in-flight, retry next watch tick.
                    still_open.append(f)
                    continue
            if status.overall == "failure":
                self.gh.add_label(pr, self.cfg.escalate_label)
                self.gh.comment(
                    pr,
                    "CI failed; orchestrator is leaving this PR for a human to handle.",
                )
                out.append(
                    (
                        f.cycle_id,
                        ShipResult(
                            status="error",
                            task_slug=f.task_slug,
                            branch=f.branch,
                            pr_number=f.pr_number,
                            error="CI failed",
                            notes="CI failure; escalated",
                        ),
                    )
                )
                continue
            still_open.append(f)
        self._in_flight = still_open
        return out

    def restore_in_flight(
        self, items: list[tuple[int, int, str, str]]
    ) -> None:
        """Re-seed the in-flight list from external state (e.g. on startup).

        Each item is (cycle_id, pr_number, branch, task_slug).
        """
        for cycle_id, pr_number, branch, task_slug in items:
            self._in_flight.append(
                _InFlight(
                    cycle_id=cycle_id,
                    pr_number=pr_number,
                    branch=branch,
                    task_slug=task_slug,
                )
            )

    # ----------------------------------------------------------- internals

    def _commit_message(self, task: Task) -> str:
        kind = task.fields.get("Type", "").strip().lower()
        prefix = self.cfg.type_prefix_map.get(kind, "chore")
        return self.cfg.commit_message_template.format(
            prefix=prefix, title=task.title, slug=task.slug
        )

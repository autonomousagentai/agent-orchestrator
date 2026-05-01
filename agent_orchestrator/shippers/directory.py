"""Directory shipper: copy a cycle's changed files into a deliverables tree.

Use this for non-code work (marketing copy, research notes, briefs, etc.)
where there's no git repo to push to. The shipper:

  1. Creates `<deliverables_dir>/<task-slug>/` (versioning if it already exists).
  2. Copies every changed path from the workspace into it, preserving the
     workspace's relative structure.
  3. Drops a `_TASK.md` index file with the task's title, fields, and
     acceptance criteria so the deliverable stands on its own.
  4. After shipping, calls `workspace.reset_after_no_progress()` so the next
     cycle starts clean — the work is preserved in the deliverables tree.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..backlog import BacklogProvider, Task
from ..workspaces import Workspace
from .base import ShipResult, Shipper

log = logging.getLogger(__name__)


@dataclass
class DirectoryShipperConfig:
    deliverables_dir: Path
    # If a deliverable for this slug already exists, suffix the new one with
    # `-vN` rather than overwriting it. Default: True.
    version_existing: bool = True
    # Reset the workspace after shipping so the next cycle is clean. Default:
    # True; turn off for workspaces where you want files to accumulate.
    reset_workspace_after_ship: bool = True


class DirectoryShipper(Shipper):
    def __init__(self, cfg: DirectoryShipperConfig, backlog: BacklogProvider) -> None:
        self.cfg = cfg
        self.backlog = backlog
        cfg.deliverables_dir.mkdir(parents=True, exist_ok=True)

    def ship(
        self,
        *,
        task: Task,
        workspace: Workspace,
        cycle_id: int,
        changed_paths: list[str],
        cycle_notes: str = "",
    ) -> ShipResult:
        if not changed_paths:
            return ShipResult(status="no_progress", task_slug=task.slug)

        target = self._target_dir(task)
        target.mkdir(parents=True, exist_ok=True)

        copied: list[str] = []
        for rel in changed_paths:
            src = workspace.path / rel
            if not src.exists():
                continue  # the agent may have deleted a file during the cycle
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
                copied.append(rel)
            except Exception:
                log.exception("could not copy %s -> %s", src, dst)

        index = target / "_TASK.md"
        index.write_text(self.backlog.render_summary(task), encoding="utf-8")
        log.info(
            "[cycle %d] shipped %s -> %s (%d files)",
            cycle_id,
            task.slug,
            target,
            len(copied),
        )

        if self.cfg.reset_workspace_after_ship:
            try:
                workspace.reset_after_no_progress()
            except Exception:
                log.exception("workspace reset after ship failed (non-fatal)")

        return ShipResult(
            status="shipped",
            task_slug=task.slug,
            target_path=str(target),
            notes=f"copied {len(copied)} file(s) to {target}",
        )

    # ----------------------------------------------------------- internals

    def _target_dir(self, task: Task) -> Path:
        base = self.cfg.deliverables_dir / task.slug
        if not self.cfg.version_existing or not base.exists():
            return base
        n = 2
        while True:
            candidate = self.cfg.deliverables_dir / f"{task.slug}-v{n}"
            if not candidate.exists():
                return candidate
            n += 1

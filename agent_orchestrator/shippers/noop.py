"""No-op shipper: just record that the cycle produced output.

Useful when the agent's job is to *update the backlog itself* (e.g. an agent
that triages and prioritizes a task list) and nothing else needs to happen
downstream.
"""

from __future__ import annotations

from ..backlog import Task
from ..workspaces import Workspace
from .base import ShipResult, Shipper


class NoopShipper(Shipper):
    def ship(
        self,
        *,
        task: Task,
        workspace: Workspace,
        cycle_id: int,
        changed_paths: list[str],
        cycle_notes: str = "",
    ) -> ShipResult:
        return ShipResult(
            status="shipped",
            task_slug=task.slug,
            notes=f"noop: {len(changed_paths)} file(s) changed in workspace",
        )

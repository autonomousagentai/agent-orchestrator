"""Shipper abstraction.

A shipper is what the orchestrator calls when a cycle produces a shippable
result (a task transitioned to "done" AND the workspace has changed files).

Different task types want different "ship" semantics:
  - GitPRShipper: rename branch, commit, push, open PR, watch CI, squash-merge
  - DirectoryShipper: copy changed files into a deliverables/<slug>/ tree
  - NoopShipper: just record success

The orchestrator calls `ship()` once a cycle has produced output, and
`watch()` periodically so long-running shippers (CI watchers) can advance
their state machines without blocking the cycle loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..backlog import Task
from ..workspaces import Workspace


@dataclass
class ShipResult:
    """What happened when we tried to ship.

    `status` semantics:
      - "shipped": fully delivered (PR merged, files copied, etc.)
      - "in_flight": handed off to an external system (PR open, CI running)
      - "no_progress": nothing to ship (no changes / no done flip)
      - "error": something failed; check `error`
    """

    status: str
    task_slug: Optional[str] = None
    branch: Optional[str] = None
    pr_number: Optional[int] = None
    target_path: Optional[str] = None  # e.g. deliverables dir, for DirectoryShipper
    error: Optional[str] = None
    notes: str = ""


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
        """Deliver the work done in `workspace` for `task`."""

    def watch(self) -> list[tuple[int, ShipResult]]:
        """Advance any in-flight shipments. Returns (cycle_id, result) pairs
        for each shipment whose status changed.

        Default: nothing to watch.
        """
        return []

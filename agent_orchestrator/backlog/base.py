"""Backlog abstractions.

A `Task` is a single unit of work with a stable `slug`, a human title, a
free-form `fields` dict, and optional acceptance bullets. The orchestrator
treats the backlog as opaque except for two questions:

  1. Which tasks just transitioned to "done" between two snapshots?
  2. What does this task look like in human-readable form (used in PR
     descriptions, deliverable index files, etc.)?

`BacklogProvider` is the seam where you plug in a different source — a
markdown file (default), a YAML file, a Notion database, a Linear API, etc.
The orchestrator only ever calls `snapshot()` and `diff_done()`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Task:
    """One backlog item.

    `slug` must be a stable, filesystem- and branch-safe identifier (lowercase,
    dashes). Everything downstream — branch names, deliverable directories,
    PR titles — derives from it.
    """

    slug: str
    title: str
    fields: dict[str, str] = field(default_factory=dict)
    acceptance: list[str] = field(default_factory=list)
    raw_block: str = ""

    def field(self, name: str, default: str = "") -> str:
        return self.fields.get(name, default).strip()


class BacklogProvider(ABC):
    """Pluggable backlog source.

    Concrete implementations decide how tasks are stored and what counts as
    "done"; the orchestrator only consumes snapshots.
    """

    @abstractmethod
    def snapshot(self) -> list[Task]:
        """Return the current set of tasks. Empty list if the source is missing."""

    @abstractmethod
    def is_done(self, task: Task) -> bool:
        """Whether `task` is in a terminal/completed state."""

    def diff_done(self, before: list[Task], after: list[Task]) -> list[Task]:
        """Tasks that flipped to done between `before` and `after`."""
        before_by_slug: dict[str, Task] = {t.slug: t for t in before}
        out: list[Task] = []
        for t in after:
            if not self.is_done(t):
                continue
            prev = before_by_slug.get(t.slug)
            if prev is None or not self.is_done(prev):
                out.append(t)
        return out

    def find(self, tasks: list[Task], slug: str) -> Optional[Task]:
        for t in tasks:
            if t.slug == slug:
                return t
        return None

    def render_summary(self, task: Task) -> str:
        """Human-readable summary of a task. Used for PR bodies and deliverable
        index files. Override for custom formatting; default is plain markdown.
        """
        lines: list[str] = [f"# {task.title}", "", f"_Task slug: `{task.slug}`_", ""]
        if task.fields:
            lines.append("## Fields")
            for k, v in task.fields.items():
                if k.lower() in ("acceptance", "result"):
                    continue
                lines.append(f"- **{k}:** {v}")
            lines.append("")
        if task.acceptance:
            lines.append("## Acceptance")
            for a in task.acceptance:
                lines.append(f"- {a}")
            lines.append("")
        result = task.fields.get("Result", "").strip()
        if result:
            lines.append("## Result")
            lines.append(result)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

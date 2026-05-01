"""Markdown backlog provider.

Expected schema (matches the convention from common engineering backlogs but
generic enough for any task list — marketing, research, content, etc.):

    ### <slug>: <Title>

    - **Status:** Ready
    - **Priority:** P1
    - **Owner:** anyone
    - **Acceptance:**
      - bullet
      - bullet
    - **Result:** (filled in by the agent when complete)

The status field name and the set of values that count as "done" are both
configurable, so you can use Status/State/Stage/Phase, and Done/Shipped/
Complete/Closed/Published, etc.

Anything between an `### <slug>:` heading and the next `### `, `## ` heading,
or `---` separator is treated as the task's block. Acceptance bullets are
collected when the field key is the configured acceptance field.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from .base import BacklogProvider, Task

_HEADING_RE = re.compile(r"^###\s+([A-Za-z0-9][A-Za-z0-9._-]*)\s*:\s*(.+?)\s*$")
_FIELD_RE = re.compile(r"^-\s+\*\*([A-Za-z][A-Za-z0-9\- ]*)\:\*\*\s*(.*?)\s*$")


class MarkdownBacklog(BacklogProvider):
    """Parse a markdown file into Task objects.

    Args:
        path: Path to the markdown backlog file.
        status_field: Name of the field that carries task status. Default
            "Status". Compared case-insensitively when reading.
        done_values: Status values that count as terminal. Default
            ("Done",). Compared case-insensitively.
        acceptance_field: Name of the field whose value is a bullet list.
            Default "Acceptance".
    """

    def __init__(
        self,
        path: Path | str,
        *,
        status_field: str = "Status",
        done_values: Iterable[str] = ("Done",),
        acceptance_field: str = "Acceptance",
    ) -> None:
        self.path = Path(path)
        self.status_field = status_field
        self._status_field_lc = status_field.lower()
        self._done_values_lc = {v.lower() for v in done_values}
        self.acceptance_field = acceptance_field
        self._acceptance_field_lc = acceptance_field.lower()

    def snapshot(self) -> list[Task]:
        if not self.path.exists():
            return []
        return list(self._parse(self.path.read_text(encoding="utf-8")))

    def is_done(self, task: Task) -> bool:
        # Look up the status field case-insensitively so users can write
        # "Status:" or "status:" in their backlog.
        for k, v in task.fields.items():
            if k.lower() == self._status_field_lc:
                return v.strip().lower() in self._done_values_lc
        return False

    # ------------------------------------------------------------------ parser

    def _parse(self, text: str) -> Iterable[Task]:
        cur: Optional[Task] = None
        block_lines: list[str] = []
        in_acceptance = False

        def flush() -> Optional[Task]:
            nonlocal cur, block_lines
            out = cur
            if out is not None:
                out.raw_block = "\n".join(block_lines).rstrip()
            cur = None
            block_lines = []
            return out

        for line in text.splitlines():
            heading = _HEADING_RE.match(line)
            if heading:
                done = flush()
                if done is not None:
                    yield done
                cur = Task(slug=heading.group(1), title=heading.group(2))
                block_lines = [line]
                in_acceptance = False
                continue
            if cur is None:
                continue
            block_lines.append(line)
            if line.startswith("## ") or line.strip() == "---":
                done = flush()
                if done is not None:
                    yield done
                in_acceptance = False
                continue
            fm = _FIELD_RE.match(line)
            if fm:
                key = fm.group(1).strip()
                val = fm.group(2).strip()
                # Strip surrounding asterisks/backticks left over from inline markup
                val = re.sub(r"^[`*]+|[`*]+$", "", val).strip()
                cur.fields[key] = val
                in_acceptance = key.lower() == self._acceptance_field_lc
                continue
            if in_acceptance:
                stripped = line.strip()
                if stripped.startswith(("- ", "* ")):
                    cur.acceptance.append(stripped[2:].strip())
                elif stripped == "":
                    continue
                else:
                    in_acceptance = False
        done = flush()
        if done is not None:
            yield done

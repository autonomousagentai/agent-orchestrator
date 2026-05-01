"""PreToolUse hooks that confine the agent to its workspace and block
destructive shell commands.

`make_path_guard` is appropriate for any workspace.

`make_bash_guard` is appropriate when the workspace is a git checkout — it
adds a denylist for destructive system commands and refuses any `git push`
that targets the protected base branch.

Both factories return async hook callbacks suitable for `ClaudeAgentOptions`'
`hooks` dict.
"""

from __future__ import annotations

import logging
import re
import shlex
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger(__name__)

_FILE_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit"}

_HARD_BLOCKED_BASH = [
    re.compile(r"\brm\s+-rf?\s+/(?!\S)"),         # rm -rf /
    re.compile(r"\brm\s+-rf?\s+/[^/\s]"),          # rm -rf /something at root
    re.compile(r"\b(mkfs|fdisk|dd\s+if=)"),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"),     # fork bomb
    re.compile(r"\bshutdown\b|\breboot\b|\bpoweroff\b"),
]


def _allow() -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _path_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def make_path_guard(
    workspace_dir: Path,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Block file operations whose path resolves outside `workspace_dir`."""

    async def hook(
        input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        tool_name = input_data.get("tool_name", "")
        if tool_name not in _FILE_TOOLS:
            return _allow()
        tool_input = input_data.get("tool_input") or {}
        fp = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not fp:
            return _allow()
        path = Path(fp)
        if not path.is_absolute():
            path = (workspace_dir / path).resolve()
        if not _path_inside(path, workspace_dir):
            log.warning("blocked %s on %s (outside %s)", tool_name, path, workspace_dir)
            return _deny(
                f"Path '{fp}' resolves outside the workspace ({workspace_dir})."
            )
        return _allow()

    return hook


def make_bash_guard(
    workspace_dir: Path,
    base_branch: Optional[str] = None,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Block destructive shell commands and pushes to the protected base.

    `base_branch` is optional — if None, only the destructive-command denylist
    applies (use this for non-git workspaces).
    """

    async def hook(
        input_data: dict[str, Any], tool_use_id: str | None, context: Any
    ) -> dict[str, Any]:
        if input_data.get("tool_name") != "Bash":
            return _allow()
        cmd = (input_data.get("tool_input") or {}).get("command") or ""

        for pat in _HARD_BLOCKED_BASH:
            if pat.search(cmd):
                return _deny(
                    f"Bash command blocked by safety hook (matched {pat.pattern!r})."
                )

        if base_branch:
            for piece in re.split(r"&&|;|\|\|", cmd):
                try:
                    parts = shlex.split(piece)
                except ValueError:
                    continue
                if len(parts) < 2 or parts[0] != "git":
                    continue
                if "push" not in parts:
                    continue
                joined = " ".join(parts)
                if re.search(rf"\b{re.escape(base_branch)}\b", joined):
                    return _deny(
                        f"Refusing `git push` that targets the protected base branch "
                        f"'{base_branch}'."
                    )
        return _allow()

    return hook

"""One-cycle Claude SDK runner.

The orchestrator main loop calls `run_cycle()` per iteration. The runner
spins up a `ClaudeSDKClient` rooted at the workspace dir, sends a single
prompt (the user's cycle template, with `{...}` placeholders filled in),
streams the response into a JSONL transcript, and returns cost + final
text.

The prompt template is fully user-defined — that's the main extensibility
hook. A coding project's prompt might say "act as the orchestrator subagent
and dispatch developer/qa"; a marketing project's prompt might say "pick the
top-priority Ready task and write the deliverable into outputs/<slug>/".

Available placeholders:
  {cycle_id}      - integer cycle number
  {workspace}     - absolute workspace path
  {backlog_path}  - absolute path to the backlog file (if known)
  {skip_slugs}    - comma-separated slugs the runner should not pick
                    (because they're already in flight); empty string if none
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    TextBlock,
)

log = logging.getLogger(__name__)


DEFAULT_ALLOWED_TOOLS = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "TodoWrite",
    "Task",
]


@dataclass
class CycleResult:
    success: bool
    cost_usd: float
    final_text: str
    transcript_path: Path
    error: Optional[str] = None


@dataclass
class CycleConfig:
    """Per-run knobs the orchestrator passes into `run_cycle`."""

    cycle_id: int
    workspace: Path
    backlog_path: Optional[Path]
    logs_dir: Path
    model: str
    max_turns: int
    budget_usd: float
    prompt_template: str
    allowed_tools: list[str]
    permission_mode: str = "acceptEdits"
    setting_sources: list[str] | None = None
    system_prompt: Optional[str] = None
    skip_slugs: list[str] | None = None
    extra_env: dict[str, str] | None = None
    pretooluse_hooks: list[tuple[str, Any]] | None = None
    cycle_done_marker: str = "CYCLE_DONE"


async def run_cycle(cfg: CycleConfig) -> CycleResult:
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    transcript = cfg.logs_dir / f"cycle-{cfg.cycle_id}.jsonl"
    log.info(
        "[cycle %d] cwd=%s budget=$%.2f model=%s -> %s",
        cfg.cycle_id,
        cfg.workspace,
        cfg.budget_usd,
        cfg.model,
        transcript,
    )

    env = {**os.environ}
    if cfg.extra_env:
        env.update(cfg.extra_env)

    hooks: dict[str, list[HookMatcher]] = {}
    if cfg.pretooluse_hooks:
        hooks["PreToolUse"] = [
            HookMatcher(matcher=matcher, hooks=[hook])
            for matcher, hook in cfg.pretooluse_hooks
        ]

    options_kwargs: dict[str, Any] = dict(
        cwd=str(cfg.workspace),
        model=cfg.model,
        max_turns=cfg.max_turns,
        max_budget_usd=cfg.budget_usd,
        allowed_tools=cfg.allowed_tools,
        permission_mode=cfg.permission_mode,
        env=env,
    )
    if cfg.setting_sources:
        options_kwargs["setting_sources"] = cfg.setting_sources
    if cfg.system_prompt:
        options_kwargs["system_prompt"] = cfg.system_prompt
    if hooks:
        options_kwargs["hooks"] = hooks

    options = ClaudeAgentOptions(**options_kwargs)

    prompt = _format_prompt(cfg)

    final_text = ""
    cost = 0.0
    success = False
    error: Optional[str] = None
    started = time.time()

    try:
        async with ClaudeSDKClient(options=options) as client:
            with transcript.open("w", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "event": "start",
                            "cycle_id": cfg.cycle_id,
                            "ts": started,
                            "workspace": str(cfg.workspace),
                            "model": cfg.model,
                            "budget_usd": cfg.budget_usd,
                        }
                    )
                    + "\n"
                )
                await client.query(prompt)
                async for msg in client.receive_response():
                    f.write(json.dumps(_msg_to_dict(msg), default=str) + "\n")
                    f.flush()
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                final_text = block.text
                    elif isinstance(msg, ResultMessage):
                        cost = float(getattr(msg, "total_cost_usd", 0.0) or 0.0)
                        success = not bool(getattr(msg, "is_error", False))
                        if not success:
                            error = (
                                getattr(msg, "result", None) or "agent reported error"
                            )
        return CycleResult(
            success=success,
            cost_usd=cost,
            final_text=final_text,
            transcript_path=transcript,
            error=error,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("[cycle %d] crashed", cfg.cycle_id)
        return CycleResult(
            success=False,
            cost_usd=cost,
            final_text=final_text,
            transcript_path=transcript,
            error=f"{type(e).__name__}: {e}",
        )


def parse_cycle_outcome(final_text: str, marker: str = "CYCLE_DONE") -> dict[str, str]:
    """Parse `<marker> slug=... outcome=...` from the agent's final message.

    Returns the parsed key=value pairs as a dict, or {} if the marker line
    is absent or malformed. Robust to extra whitespace and unquoted values.
    """
    out: dict[str, str] = {}
    for line in final_text.splitlines():
        line = line.strip()
        if not line.startswith(marker):
            continue
        for tok in line.split()[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                out[k.strip()] = v.strip()
    return out


# ----------------------------------------------------------------- internals


def _format_prompt(cfg: CycleConfig) -> str:
    skip_slugs = cfg.skip_slugs or []
    return cfg.prompt_template.format(
        cycle_id=cfg.cycle_id,
        workspace=str(cfg.workspace),
        backlog_path=str(cfg.backlog_path) if cfg.backlog_path else "",
        skip_slugs=", ".join(sorted(skip_slugs)),
    )


def _msg_to_dict(msg) -> dict:
    out: dict = {"type": type(msg).__name__}
    for attr in (
        "content",
        "text",
        "name",
        "role",
        "result",
        "total_cost_usd",
        "duration_ms",
        "is_error",
        "session_id",
        "stop_reason",
    ):
        if hasattr(msg, attr):
            v = getattr(msg, attr)
            try:
                json.dumps(v, default=str)
                out[attr] = v
            except TypeError:
                out[attr] = repr(v)
    return out

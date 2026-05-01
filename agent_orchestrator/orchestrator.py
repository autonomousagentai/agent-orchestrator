"""Main loop.

Cycle pseudocode (per iteration):

    if budget exhausted: sleep, continue
    if too many no_progress in a row: sleep, continue
    workspace.prepare(cycle_id)
    before = backlog.snapshot()
    skip_slugs = shipper.in_flight_slugs() if available
    result = run_cycle(...)
    after = backlog.snapshot()
    done_now = backlog.diff_done(before, after)
    changed_paths = workspace.changed_paths()
    if done_now and changed_paths:
        ship_result = shipper.ship(...)
    else:
        workspace.reset_after_no_progress()
        record no_progress

A second concurrent task calls `shipper.watch()` periodically so long-running
shippers (PR + CI) can advance state without blocking the cycle loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .backlog import Task
from .config import Config
from .cycle import CycleConfig, parse_cycle_outcome, run_cycle
from .factory import Runtime, build_runtime
from .hooks import make_bash_guard, make_path_guard
from .shippers import ShipResult
from .state import (
    ERROR,
    IN_FLIGHT,
    NO_PROGRESS,
    SHIPPED,
    State,
)
from .workspaces import GitWorkspace

log = logging.getLogger("agent_orchestrator")


def setup_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(logs_dir / "orchestrator.log"),
    ]
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
    )


@dataclass
class _CycleArtifacts:
    before: list[Task]
    after: list[Task]
    done_now: list[Task]
    changed_paths: list[str]
    cost_usd: float
    final_text: str
    error: Optional[str]
    transcript: Path


class Orchestrator:
    def __init__(self, cfg: Config, runtime: Optional[Runtime] = None) -> None:
        self.cfg = cfg
        cfg.ensure_dirs()
        self.state = State(cfg.paths.db_path)
        self.runtime: Runtime = runtime or build_runtime(cfg)
        self._stopping = asyncio.Event()
        self._restore_in_flight()

    def _restore_in_flight(self) -> None:
        """On startup, hand any in_flight cycles back to the shipper so its
        watch loop can keep advancing them. Avoids dangling PRs after restart.
        """
        restore = getattr(self.runtime.shipper, "restore_in_flight", None)
        if not callable(restore):
            return
        items: list[tuple[int, int, str, str]] = []
        for r in self.state.in_flight_cycles():
            if r["pr_number"] is None:
                continue
            items.append(
                (int(r["id"]), int(r["pr_number"]), r["branch"] or "", r["task_slug"] or "")
            )
        if items:
            log.info("restoring %d in-flight cycle(s) into shipper", len(items))
            restore(items)

    # ------------------------------------------------------------- top-level

    async def run(self) -> None:
        log.info(
            "agent-orchestrator starting; backlog=%s, workspace=%s, shipper=%s, "
            "daily=$%.2f, per-cycle=$%.2f",
            self.cfg.backlog.path,
            self.cfg.workspace.type,
            self.cfg.shipper.type,
            self.cfg.orchestrator.daily_budget_usd,
            self.cfg.orchestrator.cycle_budget_usd,
        )
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._signal_handler)
            except NotImplementedError:  # pragma: no cover  (Windows)
                pass

        await asyncio.gather(self._cycle_loop(), self._watch_loop())

    async def run_once(self) -> None:
        """Run a single cycle and return. Useful for cron, CI, smoke tests."""
        await self._run_one_cycle()
        # Drain the shipper once so any in-flight result lands.
        self._sync_watch_results(self.runtime.shipper.watch())

    def _signal_handler(self) -> None:
        log.info("shutdown signal received; finishing in-flight work then exiting")
        self._stopping.set()

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return

    # ----------------------------------------------------------- cycle loop

    async def _cycle_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                if self._budget_exhausted():
                    log.warning(
                        "daily budget hit ($%.2f); pausing cycles",
                        self.state.today_spend(),
                    )
                    await self._sleep_or_stop(
                        self.cfg.orchestrator.poll_interval_seconds * 5
                    )
                    continue
                if self._paused_for_no_progress():
                    await self._sleep_or_stop(
                        self.cfg.orchestrator.poll_interval_seconds
                    )
                    continue
                await self._run_one_cycle()
            except Exception:
                log.exception("cycle loop error")
            await self._sleep_or_stop(self.cfg.orchestrator.poll_interval_seconds)

    def _budget_exhausted(self) -> bool:
        return (
            self.state.today_spend() >= self.cfg.orchestrator.daily_budget_usd
        )

    def _paused_for_no_progress(self) -> bool:
        n = self.state.consecutive_no_progress()
        paused = n >= self.cfg.orchestrator.no_progress_pause_after
        if paused:
            log.warning(
                "loop paused: %d consecutive no_progress cycles >= threshold %d. "
                "Inspect the backlog, then either: clear bad rows from state.db, "
                "raise no_progress_pause_after, or restart.",
                n,
                self.cfg.orchestrator.no_progress_pause_after,
            )
        return paused

    async def _run_one_cycle(self) -> None:
        ws = self.runtime.workspace
        backlog = self.runtime.backlog

        # Allocate the cycle row first so transcripts and any prepare-time
        # failure get tracked.
        transcript_placeholder = self.cfg.paths.logs_dir / "pending.jsonl"
        cycle_id = self.state.start_cycle(str(transcript_placeholder))

        # Prepare the workspace BEFORE reading the backlog: when the backlog
        # lives inside the workspace (e.g. a cloned repo), the clone may not
        # exist yet on the first cycle.
        try:
            ws.prepare(cycle_id)
        except Exception as e:
            log.exception("[cycle %d] workspace prepare failed", cycle_id)
            self.state.finish_cycle(
                cycle_id,
                status=ERROR,
                cost_usd=0.0,
                error=f"workspace prepare failed: {e}",
            )
            return

        before = backlog.snapshot()
        if not before:
            log.warning(
                "[cycle %d] backlog %s missing or empty; nothing to do",
                cycle_id,
                self.cfg.backlog.path,
            )
            self.state.finish_cycle(
                cycle_id,
                status=NO_PROGRESS,
                cost_usd=0.0,
                notes="backlog missing or empty",
            )
            await self._sleep_or_stop(self.cfg.orchestrator.poll_interval_seconds)
            return

        # Skip-list of in-flight slugs (avoid re-doing work that already
        # has a PR open / deliverable shipped that the shipper hasn't merged).
        skip_slugs: list[str] = []
        try:
            in_flight = getattr(self.runtime.shipper, "in_flight_slugs", None)
            if callable(in_flight):
                skip_slugs = list(in_flight())
        except Exception:
            log.exception("could not fetch in-flight slugs; proceeding without skip list")
        if skip_slugs:
            log.info("[cycle %d] skipping in-flight slugs: %s", cycle_id, skip_slugs)

        remaining = (
            self.cfg.orchestrator.daily_budget_usd - self.state.today_spend()
        )
        cycle_budget = max(
            0.1, min(self.cfg.orchestrator.cycle_budget_usd, remaining)
        )

        cycle_cfg = CycleConfig(
            cycle_id=cycle_id,
            workspace=ws.path,
            backlog_path=self.runtime.backlog_path_inside_workspace,
            logs_dir=self.cfg.paths.logs_dir,
            model=self.cfg.agents.model,
            max_turns=self.cfg.agents.max_turns,
            budget_usd=cycle_budget,
            prompt_template=self.cfg.agents.prompt_template,
            allowed_tools=list(self.cfg.agents.allowed_tools),
            permission_mode=self.cfg.agents.permission_mode,
            setting_sources=list(self.cfg.agents.setting_sources) or None,
            system_prompt=self.cfg.agents.system_prompt,
            skip_slugs=skip_slugs,
            pretooluse_hooks=self._build_hooks(),
            cycle_done_marker=self.cfg.agents.cycle_done_marker,
        )

        log.info("[cycle %d] dispatching; budget=$%.2f", cycle_id, cycle_budget)
        result = await run_cycle(cycle_cfg)
        self.state.add_spend(result.cost_usd)

        after = backlog.snapshot()
        done_now = backlog.diff_done(before, after)
        changed_paths = ws.changed_paths()
        outcome = parse_cycle_outcome(
            result.final_text, marker=self.cfg.agents.cycle_done_marker
        )
        log.info(
            "[cycle %d] cost=$%.4f done_now=%d changed_paths=%d outcome=%s",
            cycle_id,
            result.cost_usd,
            len(done_now),
            len(changed_paths),
            outcome,
        )

        artifacts = _CycleArtifacts(
            before=before,
            after=after,
            done_now=done_now,
            changed_paths=changed_paths,
            cost_usd=result.cost_usd,
            final_text=result.final_text,
            error=result.error,
            transcript=result.transcript_path,
        )
        await self._finalize_cycle(cycle_id, artifacts, outcome)

    async def _finalize_cycle(
        self,
        cycle_id: int,
        a: _CycleArtifacts,
        outcome: dict[str, str],
    ) -> None:
        backlog = self.runtime.backlog
        ws = self.runtime.workspace

        # Pick the task to ship: prefer the slug the agent told us in its
        # CYCLE_DONE line, otherwise fall back to the first done_now task.
        ship: Optional[Task] = None
        if outcome.get("outcome") == "done":
            slug = outcome.get("slug", "")
            ship = backlog.find(a.done_now, slug)
        if ship is None and a.done_now:
            ship = a.done_now[0]

        if ship is None or not a.changed_paths:
            log.info(
                "[cycle %d] no_progress (no done flip or no changes)", cycle_id
            )
            try:
                ws.reset_after_no_progress()
            except Exception:
                log.exception(
                    "[cycle %d] workspace reset after no-progress failed", cycle_id
                )
            self.state.finish_cycle(
                cycle_id,
                status=NO_PROGRESS if not a.error else ERROR,
                cost_usd=a.cost_usd,
                error=a.error,
                notes=a.final_text[:1000],
            )
            return

        try:
            ship_result = self.runtime.shipper.ship(
                task=ship,
                workspace=ws,
                cycle_id=cycle_id,
                changed_paths=a.changed_paths,
                cycle_notes=a.final_text[:2000],
            )
        except Exception as e:
            log.exception("[cycle %d] shipper raised", cycle_id)
            ship_result = ShipResult(
                status="error", task_slug=ship.slug, error=f"shipper raised: {e}"
            )

        self._record_ship_result(cycle_id, a, ship_result, ship)

    def _record_ship_result(
        self,
        cycle_id: int,
        a: _CycleArtifacts,
        result: ShipResult,
        task: Task,
    ) -> None:
        status_map = {
            "shipped": SHIPPED,
            "in_flight": IN_FLIGHT,
            "no_progress": NO_PROGRESS,
            "error": ERROR,
        }
        self.state.finish_cycle(
            cycle_id,
            status=status_map.get(result.status, ERROR),
            cost_usd=a.cost_usd,
            task_slug=result.task_slug or task.slug,
            branch=result.branch,
            pr_number=result.pr_number,
            target_path=result.target_path,
            error=result.error,
            notes=result.notes or a.final_text[:1000],
        )

    # ----------------------------------------------------------- watch loop

    async def _watch_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                self._sync_watch_results(self.runtime.shipper.watch())
            except Exception:
                log.exception("watch loop error")
            await self._sleep_or_stop(
                self.cfg.orchestrator.watch_interval_seconds
            )

    def _sync_watch_results(
        self, results: list[tuple[int, ShipResult]]
    ) -> None:
        status_map = {
            "shipped": SHIPPED,
            "in_flight": IN_FLIGHT,
            "no_progress": NO_PROGRESS,
            "error": ERROR,
        }
        for cycle_id, r in results:
            self.state.update_cycle(
                cycle_id,
                status=status_map.get(r.status),
                pr_number=r.pr_number,
                target_path=r.target_path,
                notes=r.notes,
                error=r.error,
            )

    # --------------------------------------------------------------- hooks

    def _build_hooks(self) -> list[tuple[str, object]]:
        ws = self.runtime.workspace
        path_guard = make_path_guard(ws.path)
        base_branch = None
        if isinstance(ws, GitWorkspace):
            base_branch = ws.cfg.base_branch
        bash_guard = make_bash_guard(ws.path, base_branch)
        return [
            ("Read|Write|Edit|MultiEdit|NotebookEdit", path_guard),
            ("Bash", bash_guard),
        ]

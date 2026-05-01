"""SQLite cycle + spend tracking.

Generic across shippers: a `cycle` row records what task was attempted, what
the shipper produced (PR number / deliverable path / nothing), and the cost.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

# cycle.status values
RUNNING = "running"
NO_PROGRESS = "no_progress"
SHIPPED = "shipped"
IN_FLIGHT = "in_flight"      # e.g. PR opened, CI pending
ERROR = "error"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    status              TEXT NOT NULL,
    cost_usd            REAL NOT NULL DEFAULT 0.0,
    task_slug           TEXT,
    branch              TEXT,
    pr_number           INTEGER,
    target_path         TEXT,
    error               TEXT,
    transcript_path     TEXT,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS daily_spend (
    day                 TEXT PRIMARY KEY,
    cost_usd            REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_cycles_status ON cycles(status);
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


class State:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    # --------------------------------------------------------------- cycles

    def start_cycle(self, transcript_path: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO cycles (started_at, status, transcript_path) VALUES (?, ?, ?)",
                (_now(), RUNNING, transcript_path),
            )
            return cur.lastrowid  # type: ignore[return-value]

    def finish_cycle(
        self,
        cycle_id: int,
        *,
        status: str,
        cost_usd: float,
        task_slug: Optional[str] = None,
        branch: Optional[str] = None,
        pr_number: Optional[int] = None,
        target_path: Optional[str] = None,
        error: Optional[str] = None,
        notes: str = "",
    ) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE cycles SET finished_at=?, status=?, cost_usd=?, task_slug=?, "
                "branch=?, pr_number=?, target_path=?, error=?, notes=? WHERE id=?",
                (
                    _now(),
                    status,
                    cost_usd,
                    task_slug,
                    branch,
                    pr_number,
                    target_path,
                    error,
                    notes,
                    cycle_id,
                ),
            )

    def update_cycle(
        self,
        cycle_id: int,
        *,
        status: Optional[str] = None,
        pr_number: Optional[int] = None,
        target_path: Optional[str] = None,
        notes: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        sets: list[str] = []
        vals: list = []
        if status is not None:
            sets.append("status=?")
            vals.append(status)
        if pr_number is not None:
            sets.append("pr_number=?")
            vals.append(pr_number)
        if target_path is not None:
            sets.append("target_path=?")
            vals.append(target_path)
        if notes is not None:
            sets.append("notes=?")
            vals.append(notes)
        if error is not None:
            sets.append("error=?")
            vals.append(error)
        if not sets:
            return
        vals.append(cycle_id)
        with self._conn() as c:
            c.execute(f"UPDATE cycles SET {', '.join(sets)} WHERE id=?", vals)

    def list_cycles(self, limit: int = 50) -> list[sqlite3.Row]:
        with self._conn() as c:
            return list(
                c.execute("SELECT * FROM cycles ORDER BY id DESC LIMIT ?", (limit,))
            )

    def consecutive_no_progress(self) -> int:
        with self._conn() as c:
            n = 0
            for row in c.execute("SELECT status FROM cycles ORDER BY id DESC LIMIT 50"):
                if row["status"] == NO_PROGRESS:
                    n += 1
                else:
                    break
            return n

    def in_flight_cycles(self) -> list[sqlite3.Row]:
        with self._conn() as c:
            return list(
                c.execute(
                    "SELECT * FROM cycles WHERE status=? ORDER BY id", (IN_FLIGHT,)
                )
            )

    # ---------------------------------------------------------------- spend

    def add_spend(self, cost_usd: float) -> float:
        if cost_usd <= 0:
            return self.today_spend()
        with self._conn() as c:
            c.execute(
                "INSERT INTO daily_spend(day,cost_usd) VALUES(?,?)"
                " ON CONFLICT(day) DO UPDATE SET cost_usd = cost_usd + excluded.cost_usd",
                (_today(), cost_usd),
            )
            row = c.execute(
                "SELECT cost_usd FROM daily_spend WHERE day=?", (_today(),)
            ).fetchone()
            return float(row["cost_usd"]) if row else 0.0

    def today_spend(self) -> float:
        with self._conn() as c:
            row = c.execute(
                "SELECT cost_usd FROM daily_spend WHERE day=?", (_today(),)
            ).fetchone()
            return float(row["cost_usd"]) if row else 0.0

    # -------------------------------------------------------------- helpers

    def dump(self) -> str:
        rows = self.list_cycles(limit=20)
        return json.dumps(
            [
                {
                    "id": r["id"],
                    "started": r["started_at"],
                    "finished": r["finished_at"],
                    "status": r["status"],
                    "cost_usd": round(float(r["cost_usd"] or 0.0), 4),
                    "task_slug": r["task_slug"],
                    "branch": r["branch"],
                    "pr": r["pr_number"],
                    "target": r["target_path"],
                    "error": r["error"],
                }
                for r in rows
            ],
            indent=2,
        )

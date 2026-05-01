"""Tests for the SQLite state layer."""

from __future__ import annotations

from agent_orchestrator.state import (
    ERROR,
    IN_FLIGHT,
    NO_PROGRESS,
    SHIPPED,
    State,
)


def _state(tmp_path) -> State:
    return State(tmp_path / "state.db")


def test_start_and_finish_cycle(tmp_path):
    s = _state(tmp_path)
    cid = s.start_cycle(transcript_path="logs/cycle-1.jsonl")
    assert cid == 1
    s.finish_cycle(
        cid,
        status=SHIPPED,
        cost_usd=0.42,
        task_slug="my-task",
        branch="feature/my-task",
        pr_number=99,
        notes="merged",
    )
    rows = s.list_cycles(limit=5)
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == SHIPPED
    assert r["task_slug"] == "my-task"
    assert r["pr_number"] == 99
    assert abs(float(r["cost_usd"]) - 0.42) < 1e-9


def test_consecutive_no_progress_count(tmp_path):
    s = _state(tmp_path)
    for _ in range(3):
        cid = s.start_cycle("t.jsonl")
        s.finish_cycle(cid, status=NO_PROGRESS, cost_usd=0.0)
    assert s.consecutive_no_progress() == 3

    # A non-no_progress cycle resets the counter.
    cid = s.start_cycle("t.jsonl")
    s.finish_cycle(cid, status=SHIPPED, cost_usd=0.1, task_slug="ok")
    assert s.consecutive_no_progress() == 0

    # Another no_progress builds it back up again.
    cid = s.start_cycle("t.jsonl")
    s.finish_cycle(cid, status=NO_PROGRESS, cost_usd=0.0)
    assert s.consecutive_no_progress() == 1


def test_in_flight_cycles_filter(tmp_path):
    s = _state(tmp_path)
    for status, slug in [
        (SHIPPED, "a"),
        (IN_FLIGHT, "b"),
        (NO_PROGRESS, "c"),
        (IN_FLIGHT, "d"),
        (ERROR, "e"),
    ]:
        cid = s.start_cycle("t.jsonl")
        s.finish_cycle(cid, status=status, cost_usd=0.0, task_slug=slug)
    in_flight = s.in_flight_cycles()
    assert sorted(r["task_slug"] for r in in_flight) == ["b", "d"]


def test_update_cycle_partial(tmp_path):
    s = _state(tmp_path)
    cid = s.start_cycle("t.jsonl")
    s.finish_cycle(cid, status=IN_FLIGHT, cost_usd=0.0, task_slug="x", pr_number=10)
    # Promote to shipped.
    s.update_cycle(cid, status=SHIPPED, notes="merged after CI green")
    row = s.list_cycles()[0]
    assert row["status"] == SHIPPED
    assert row["pr_number"] == 10  # untouched
    assert row["notes"] == "merged after CI green"


def test_daily_spend_accumulates(tmp_path):
    s = _state(tmp_path)
    assert s.today_spend() == 0.0
    s.add_spend(0.10)
    s.add_spend(0.25)
    s.add_spend(0.0)  # zero is a no-op
    assert abs(s.today_spend() - 0.35) < 1e-9

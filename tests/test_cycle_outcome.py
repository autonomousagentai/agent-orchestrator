"""Tests for `parse_cycle_outcome` — the CYCLE_DONE handshake parser."""

from __future__ import annotations

from agent_orchestrator.cycle import parse_cycle_outcome


def test_parses_basic_line():
    out = parse_cycle_outcome("CYCLE_DONE slug=foo-bar outcome=done")
    assert out == {"slug": "foo-bar", "outcome": "done"}


def test_finds_marker_among_other_text():
    final = """\
Did the work, ran qa, marked Done.

CYCLE_DONE slug=fix-thing outcome=done
"""
    assert parse_cycle_outcome(final) == {"slug": "fix-thing", "outcome": "done"}


def test_returns_empty_dict_when_marker_missing():
    assert parse_cycle_outcome("nothing useful here") == {}


def test_handles_extra_whitespace():
    assert parse_cycle_outcome("   CYCLE_DONE slug=x outcome=halted   ") == {
        "slug": "x",
        "outcome": "halted",
    }


def test_supports_custom_marker():
    out = parse_cycle_outcome(
        "DELIVERED slug=widget outcome=done", marker="DELIVERED"
    )
    assert out == {"slug": "widget", "outcome": "done"}


def test_only_first_marker_line_consumed_for_each_token():
    # Multiple marker lines: the parser walks them all; later values win.
    text = "CYCLE_DONE slug=a outcome=halted\nCYCLE_DONE slug=b outcome=done"
    out = parse_cycle_outcome(text)
    assert out == {"slug": "b", "outcome": "done"}


def test_outcome_with_dash():
    assert parse_cycle_outcome("CYCLE_DONE slug=t outcome=sent-back") == {
        "slug": "t",
        "outcome": "sent-back",
    }

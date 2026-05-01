"""Tests for the `DirectoryWorkspace` snapshot/diff logic."""

from __future__ import annotations

import time

from agent_orchestrator.workspaces.directory import (
    DirectoryWorkspace,
    DirectoryWorkspaceConfig,
)


def _make(tmp_path, **overrides):
    cfg = DirectoryWorkspaceConfig(
        workspace_dir=tmp_path,
        wipe_subdirs=overrides.get("wipe_subdirs", []),
        ignore_patterns=overrides.get(
            "ignore_patterns", [".git", "__pycache__"]
        ),
    )
    return DirectoryWorkspace(cfg)


def test_changed_paths_empty_after_prepare(tmp_path):
    (tmp_path / "existing.md").write_text("hello", encoding="utf-8")
    ws = _make(tmp_path)
    ws.prepare(cycle_id=1)
    assert ws.changed_paths() == []


def test_detects_new_file(tmp_path):
    (tmp_path / "existing.md").write_text("hello", encoding="utf-8")
    ws = _make(tmp_path)
    ws.prepare(cycle_id=1)
    (tmp_path / "new.md").write_text("brand new", encoding="utf-8")
    assert ws.changed_paths() == ["new.md"]


def test_detects_modified_file(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("v1", encoding="utf-8")
    ws = _make(tmp_path)
    ws.prepare(cycle_id=1)
    # Sleep briefly to ensure mtime changes (filesystem resolution).
    time.sleep(0.01)
    f.write_text("v2 different content", encoding="utf-8")
    assert ws.changed_paths() == ["doc.md"]


def test_detects_deleted_file(tmp_path):
    f = tmp_path / "doomed.md"
    f.write_text("rip", encoding="utf-8")
    ws = _make(tmp_path)
    ws.prepare(cycle_id=1)
    f.unlink()
    assert ws.changed_paths() == ["doomed.md"]


def test_ignored_patterns_excluded(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("internals", encoding="utf-8")
    ws = _make(tmp_path, ignore_patterns=[".git", "__pycache__"])
    ws.prepare(cycle_id=1)
    # Add a new .git file — should NOT show up as changed.
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    # Add a tracked file — should show up.
    (tmp_path / "tracked.md").write_text("ok", encoding="utf-8")
    changed = ws.changed_paths()
    assert "tracked.md" in changed
    assert not any(p.startswith(".git") for p in changed)


def test_wipe_subdirs_resets_them(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "old.txt").write_text("stale", encoding="utf-8")
    keep = tmp_path / "keep.md"
    keep.write_text("preserve me", encoding="utf-8")

    ws = _make(tmp_path, wipe_subdirs=["scratch"])
    ws.prepare(cycle_id=1)

    assert not (scratch / "old.txt").exists()
    assert scratch.exists()    # the dir itself is recreated empty
    assert keep.exists()       # other files survive


def test_reset_after_no_progress_removes_new_files_only(tmp_path):
    pre_existing = tmp_path / "pre.md"
    pre_existing.write_text("existed before", encoding="utf-8")
    ws = _make(tmp_path)
    ws.prepare(cycle_id=1)
    # Agent creates a new file and modifies the existing one.
    (tmp_path / "new.md").write_text("partial work", encoding="utf-8")
    pre_existing.write_text("modified, but should NOT be reverted", encoding="utf-8")

    ws.reset_after_no_progress()

    assert not (tmp_path / "new.md").exists(), "new file should be removed"
    assert pre_existing.read_text(encoding="utf-8") == \
        "modified, but should NOT be reverted", \
        "modified-existing files are left for human inspection"


def test_changed_paths_sorted(tmp_path):
    ws = _make(tmp_path)
    ws.prepare(cycle_id=1)
    (tmp_path / "z.md").write_text("z", encoding="utf-8")
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "m.md").write_text("m", encoding="utf-8")
    assert ws.changed_paths() == ["a.md", "m.md", "z.md"]

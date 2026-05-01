"""Unit tests for the markdown backlog parser."""

from __future__ import annotations

from textwrap import dedent

from agent_orchestrator.backlog.markdown import MarkdownBacklog


def _write(tmp_path, name: str, content: str):
    p = tmp_path / name
    p.write_text(dedent(content), encoding="utf-8")
    return p


def test_parses_minimal_task(tmp_path):
    p = _write(
        tmp_path,
        "BACKLOG.md",
        """\
        ### my-task: Do the thing

        - **Status:** Ready
        - **Priority:** P1
        """,
    )
    b = MarkdownBacklog(p)
    tasks = b.snapshot()
    assert len(tasks) == 1
    t = tasks[0]
    assert t.slug == "my-task"
    assert t.title == "Do the thing"
    assert t.fields["Status"] == "Ready"
    assert t.fields["Priority"] == "P1"
    assert not b.is_done(t)


def test_acceptance_bullets_collected(tmp_path):
    p = _write(
        tmp_path,
        "BACKLOG.md",
        """\
        ### foo: Bar

        - **Status:** Ready
        - **Acceptance:**
          - first thing
          - second thing
          - third thing
        """,
    )
    tasks = MarkdownBacklog(p).snapshot()
    assert tasks[0].acceptance == ["first thing", "second thing", "third thing"]


def test_done_detection_with_custom_field_and_values(tmp_path):
    p = _write(
        tmp_path,
        "BACKLOG.md",
        """\
        ### a: Item

        - **Stage:** Published

        ### b: Item B

        - **Stage:** Drafting
        """,
    )
    b = MarkdownBacklog(
        p, status_field="Stage", done_values=["Published", "Done"]
    )
    a, bb = b.snapshot()
    assert b.is_done(a)
    assert not b.is_done(bb)


def test_diff_done(tmp_path):
    p = _write(
        tmp_path,
        "BACKLOG.md",
        """\
        ### one: One

        - **Status:** Ready

        ### two: Two

        - **Status:** Ready
        """,
    )
    b = MarkdownBacklog(p)
    before = b.snapshot()
    # Simulate the agent flipping `one` to Done.
    p.write_text(
        dedent(
            """\
            ### one: One

            - **Status:** Done

            ### two: Two

            - **Status:** Ready
            """
        ),
        encoding="utf-8",
    )
    after = b.snapshot()
    flipped = b.diff_done(before, after)
    assert [t.slug for t in flipped] == ["one"]


def test_separator_terminates_block(tmp_path):
    """A `---` separator should not silently swallow the next heading."""
    p = _write(
        tmp_path,
        "BACKLOG.md",
        """\
        ### one: First

        - **Status:** Ready

        ---

        ### two: Second

        - **Status:** Ready
        """,
    )
    tasks = MarkdownBacklog(p).snapshot()
    assert [t.slug for t in tasks] == ["one", "two"]


def test_missing_file_returns_empty_list(tmp_path):
    assert MarkdownBacklog(tmp_path / "nope.md").snapshot() == []

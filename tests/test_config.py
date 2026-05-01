"""Tests for the config loader."""

from __future__ import annotations

from textwrap import dedent

import pytest

from agent_orchestrator.config import Config


def _write_config(tmp_path, body: str, prompt: str = "do the work"):
    (tmp_path / "prompts").mkdir(exist_ok=True)
    (tmp_path / "prompts" / "cycle.md").write_text(prompt, encoding="utf-8")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(dedent(body), encoding="utf-8")
    return cfg_path


def test_minimal_directory_noop_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    p = _write_config(
        tmp_path,
        """
        [orchestrator]
        daily_budget_usd = 5.0
        cycle_budget_usd = 0.5

        [paths]
        workspaces_dir = "./workspaces"
        logs_dir       = "./logs"
        db_path        = "./state.db"

        [agents]
        model       = "claude-haiku-4-5"
        max_turns   = 80
        prompt_file = "./prompts/cycle.md"

        [backlog]
        type        = "markdown"
        path        = "./QUESTIONS.md"
        done_values = ["Answered"]

        [workspace]
        type = "directory"

        [workspace.directory]
        path = "./workspace"

        [shipper]
        type = "noop"
        """,
    )

    cfg = Config.load(p)
    assert cfg.agents.model == "claude-haiku-4-5"
    assert cfg.agents.prompt_template == "do the work"
    assert cfg.backlog.done_values == ["Answered"]
    assert cfg.workspace.type == "directory"
    assert cfg.shipper.type == "noop"
    assert cfg.anthropic_api_key == "sk-test"
    # Paths are resolved relative to the config's directory.
    assert cfg.paths.workspaces_dir.is_absolute()
    assert cfg.paths.workspaces_dir.name == "workspaces"


def test_missing_api_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = _write_config(
        tmp_path,
        """
        [paths]
        workspaces_dir = "./w"
        logs_dir       = "./l"
        db_path        = "./s.db"
        [agents]
        model = "m"
        max_turns = 1
        prompt_file = "./prompts/cycle.md"
        [backlog]
        type = "markdown"
        path = "./b.md"
        [workspace]
        type = "directory"
        [workspace.directory]
        path = "./w"
        [shipper]
        type = "noop"
        """,
    )
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        Config.load(p)


def test_git_workspace_requires_github_token(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    p = _write_config(
        tmp_path,
        """
        [paths]
        workspaces_dir = "./w"
        logs_dir       = "./l"
        db_path        = "./s.db"
        [agents]
        model = "m"
        max_turns = 1
        prompt_file = "./prompts/cycle.md"
        [backlog]
        type = "markdown"
        path = "./b.md"
        [workspace]
        type = "git"
        [workspace.git]
        repo = "x/y"
        base_branch = "main"
        [shipper]
        type = "noop"
        """,
    )
    with pytest.raises(SystemExit, match="GITHUB_TOKEN"):
        Config.load(p)


def test_inline_prompt_template(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            [paths]
            workspaces_dir = "./w"
            logs_dir       = "./l"
            db_path        = "./s.db"
            [agents]
            model = "m"
            max_turns = 1
            prompt_template = "inline prompt for {cycle_id}"
            [backlog]
            type = "markdown"
            path = "./b.md"
            [workspace]
            type = "directory"
            [workspace.directory]
            path = "./w"
            [shipper]
            type = "noop"
            """
        ),
        encoding="utf-8",
    )
    cfg = Config.load(cfg_path)
    assert cfg.agents.prompt_template == "inline prompt for {cycle_id}"


def test_missing_prompt_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            [paths]
            workspaces_dir = "./w"
            logs_dir       = "./l"
            db_path        = "./s.db"
            [agents]
            model = "m"
            max_turns = 1
            [backlog]
            type = "markdown"
            path = "./b.md"
            [workspace]
            type = "directory"
            [workspace.directory]
            path = "./w"
            [shipper]
            type = "noop"
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="prompt_file or prompt_template"):
        Config.load(cfg_path)


def test_defaults_applied_for_orchestrator_section(tmp_path, monkeypatch):
    """Omitting [orchestrator] should use sensible defaults, not crash."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    p = _write_config(
        tmp_path,
        """
        [paths]
        workspaces_dir = "./w"
        logs_dir       = "./l"
        db_path        = "./s.db"
        [agents]
        model = "m"
        max_turns = 1
        prompt_file = "./prompts/cycle.md"
        [backlog]
        type = "markdown"
        path = "./b.md"
        [workspace]
        type = "directory"
        [workspace.directory]
        path = "./w"
        [shipper]
        type = "noop"
        """,
    )
    cfg = Config.load(p)
    assert cfg.orchestrator.daily_budget_usd == 25.0
    assert cfg.orchestrator.poll_interval_seconds == 60
    assert cfg.orchestrator.no_progress_pause_after == 3

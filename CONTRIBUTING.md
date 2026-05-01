# Contributing

Thanks for considering a contribution. The core of agent-orchestrator is
small (under ~1.5k lines of Python) and the interfaces are deliberately
minimal, so most useful contributions land cleanly.

## Development setup

```bash
git clone https://github.com/autonomousagentai/agent-orchestrator.git
cd agent-orchestrator
python -m venv .venv
source .venv/bin/activate
pip install -e .[git,dev]
```

`.[git,dev]` pulls in PyGithub (for the git_pr shipper) plus pytest +
ruff. Tests don't actually call PyGithub or the Claude SDK — `tests/conftest.py`
stubs both — so you can also work without the `[git]` extra.

## Run the test suite

```bash
pytest tests/ -v
```

There are about 30 tests covering the markdown backlog parser, the
directory workspace, the state layer, and the config loader. They run in
under a second.

## Lint

```bash
ruff check .
ruff format --check .
```

Run `ruff format .` to auto-fix.

## Smoke-test a real cycle

```bash
cp examples/research/config.toml.example  ./config.toml
cp -r examples/research/prompts            ./prompts
mkdir -p workspace
cp examples/research/QUESTIONS.md.example  ./workspace/QUESTIONS.md
cp .env.example .env
$EDITOR .env                                  # set ANTHROPIC_API_KEY

agent-orchestrator --once
```

If your change affects a non-trivial part of the cycle/ship/watch flow,
include the `logs/cycle-1.jsonl` (or the relevant excerpts) in your PR
description.

## What kinds of changes are easy to merge

- Bug fixes with a regression test.
- New built-in backlog providers, workspaces, or shippers — see
  [docs/extending.md](docs/extending.md). Each one is a self-contained
  addition behind the existing interfaces.
- Improvements to docs, examples, or error messages.
- Performance / cost improvements that don't sacrifice correctness.

## What kinds need discussion first

Open an issue before you write code if you're planning to:

- Change the orchestrator's interface boundaries (the `BacklogProvider` /
  `Workspace` / `Shipper` ABCs).
- Add a new top-level config section.
- Loosen a sandbox hook default.
- Add a runtime dependency.

Those changes affect every existing user, so we want to land them
deliberately.

## Style

- Python 3.11+. Use modern syntax (`list[str]`, `dict[str, Any]`, PEP 604
  union types, `match` statements where they improve clarity).
- Type-hint public functions. Private helpers may skip hints when the
  types are obvious from one-line implementations.
- Docstrings: explain the *why* of non-obvious behavior. Skip docstrings
  on small, well-named functions.
- Comments: explain non-obvious *why*, not *what*. The code already says
  what.
- No backwards-compat shims. This is pre-1.0; we'd rather change the
  interface and bump versions than carry old shapes.

## Commit messages

We follow conventional commits loosely: a short imperative subject line,
optional `feat:` / `fix:` / `docs:` / `chore:` prefix, and a body when
the change isn't obvious from the subject.

Example:

```
feat: add Linear backlog provider

Closes #14. Pulls issues with the configured ready_label, treats issues
labeled with done_label as terminal. Slug derives from issue number.
Tests cover snapshot, is_done, and the slug format.
```

## License

By contributing you agree that your contributions are licensed under the
project's MIT license.

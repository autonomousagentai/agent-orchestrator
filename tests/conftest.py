"""Shared fixtures: stub `claude_agent_sdk` and `github` so tests don't
need the SDK installed.
"""

from __future__ import annotations

import sys
import types


def _stub_claude_sdk() -> None:
    if "claude_agent_sdk" in sys.modules:
        return
    m = types.ModuleType("claude_agent_sdk")
    for name in (
        "AssistantMessage",
        "ClaudeSDKClient",
        "ResultMessage",
        "TextBlock",
    ):
        setattr(m, name, type(name, (), {}))
    m.ClaudeAgentOptions = lambda **kw: kw
    m.HookMatcher = lambda **kw: kw
    sys.modules["claude_agent_sdk"] = m


def _stub_github() -> None:
    if "github" in sys.modules:
        return
    m = types.ModuleType("github")
    m.Github = lambda *a, **kw: None
    sys.modules["github"] = m


_stub_claude_sdk()
_stub_github()

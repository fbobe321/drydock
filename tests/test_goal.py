"""Tests for the /goal autonomous-pursuit feature."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from drydock.core.goal import (
    EVALUATOR_SYSTEM_PROMPT,
    GoalState,
    build_evaluator_prompt,
    collect_recent_message_snippets,
    make_continuation_prompt,
    parse_verdict,
)


# ── GoalState ───────────────────────────────────────────────────────────

def test_goal_state_defaults():
    g = GoalState(condition="All tests pass")
    assert g.active is True
    assert g.iterations == 0
    assert g.max_iterations == 20
    assert g.remaining == 20
    assert g.last_verdict == ""


def test_goal_state_inactive_when_empty():
    g = GoalState(condition="")
    assert g.active is False
    g = GoalState(condition="   ")
    assert g.active is False


def test_goal_state_remaining_decreases():
    g = GoalState(condition="X", max_iterations=5)
    g.iterations = 3
    assert g.remaining == 2
    g.iterations = 10  # past cap
    assert g.remaining == 0


# ── parse_verdict ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected_verdict", [
    ("VERDICT: YES\nAll tests pass.", "YES"),
    ("VERDICT: NO\nThree tests still failing.", "NO"),
    ("VERDICT:YES", "YES"),
    ("verdict: yes\nreason", "YES"),
    ("YES.", "YES"),
    ("No.", "NO"),
    ("yes\n", "YES"),
])
def test_parse_verdict_recognises_clear_yes_no(raw, expected_verdict):
    v, _ = parse_verdict(raw)
    assert v == expected_verdict


def test_parse_verdict_returns_error_for_ambiguous():
    v, reason = parse_verdict("maybe?")
    assert v == "ERROR"
    assert "unrecognised" in reason


def test_parse_verdict_empty():
    v, reason = parse_verdict("")
    assert v == "ERROR"


def test_parse_verdict_extracts_reasoning_from_second_line():
    v, reason = parse_verdict("VERDICT: NO\nx.py:42 still raises TypeError")
    assert v == "NO"
    assert "x.py:42" in reason


# ── build_evaluator_prompt + system prompt ──────────────────────────────

def test_evaluator_system_prompt_requires_strict_format():
    """The system prompt must constrain the evaluator output enough
    that parse_verdict can read its FIRST line as VERDICT:YES/NO."""
    assert "VERDICT: YES" in EVALUATOR_SYSTEM_PROMPT
    assert "VERDICT: NO" in EVALUATOR_SYSTEM_PROMPT
    assert "FIRST line" in EVALUATOR_SYSTEM_PROMPT


def test_build_evaluator_prompt_includes_goal_and_snippets():
    g = GoalState(condition="x.py:42 returns 0 for empty input")
    out = build_evaluator_prompt(g, ["[user] write fix", "[assistant] done"])
    assert "x.py:42 returns 0 for empty input" in out
    assert "[user]" in out
    assert "[assistant]" in out


def test_build_evaluator_prompt_truncates_long_conversations():
    g = GoalState(condition="X")
    huge = ["[user] " + "Z" * 2000] * 10
    out = build_evaluator_prompt(g, huge)
    # The function caps total conversation context at 8000 chars
    assert len(out) < 9000


# ── make_continuation_prompt ────────────────────────────────────────────

def test_continuation_prompt_mentions_goal_and_iteration():
    g = GoalState(condition="All linters pass", max_iterations=10)
    g.iterations = 4
    out = make_continuation_prompt(g)
    assert "All linters pass" in out
    assert "4/10" in out
    assert "auto-clear" in out


# ── collect_recent_message_snippets ─────────────────────────────────────

class _FakeRole:
    def __init__(self, v):
        self.value = v


class _FakeMsg:
    def __init__(self, role, content="", tool_calls=None):
        self.role = _FakeRole(role)
        self.content = content
        self.tool_calls = tool_calls


def test_collect_snippets_keeps_last_n_and_truncates_long():
    msgs = [_FakeMsg("user", f"prompt {i}") for i in range(20)]
    out = collect_recent_message_snippets(msgs, n=5)
    assert len(out) == 5
    assert "prompt 15" in out[0]
    assert "prompt 19" in out[-1]


def test_collect_snippets_summarises_tool_calls_when_content_empty():
    tc1 = MagicMock()
    tc1.function.name = "read_file"
    tc2 = MagicMock()
    tc2.function.name = "bash"
    msg = _FakeMsg("assistant", content="", tool_calls=[tc1, tc2])
    out = collect_recent_message_snippets([msg], n=5)
    assert len(out) == 1
    assert "read_file" in out[0]
    assert "bash" in out[0]


def test_collect_snippets_truncates_each_content_to_600():
    msg = _FakeMsg("assistant", "x" * 2000)
    out = collect_recent_message_snippets([msg], n=1)
    assert len(out[0]) < 700  # role tag + 600 chars + truncation marker


# ── Command registry ────────────────────────────────────────────────────

def test_goal_command_registered():
    from drydock.cli.commands import CommandRegistry
    r = CommandRegistry()
    cmd = r.commands.get("goal")
    assert cmd is not None
    assert "/goal" in cmd.aliases
    assert cmd.handler == "_goal_command"


# ── AgentLoop set_goal / clear_goal / evaluate_goal API ────────────────

def test_agent_loop_exposes_goal_api():
    from drydock.core.agent_loop import AgentLoop
    assert callable(getattr(AgentLoop, "set_goal", None))
    assert callable(getattr(AgentLoop, "clear_goal", None))
    assert callable(getattr(AgentLoop, "evaluate_goal", None))


def test_set_goal_creates_active_state():
    """Smoke test of set_goal/clear_goal on a stubbed AgentLoop."""
    from drydock.core.agent_loop import AgentLoop
    loop = MagicMock(spec=AgentLoop)
    # bind the real methods
    loop.set_goal = AgentLoop.set_goal.__get__(loop)
    loop.clear_goal = AgentLoop.clear_goal.__get__(loop)
    loop.goal = None

    loop.set_goal("Make tests pass")
    assert loop.goal is not None
    assert loop.goal.active
    assert loop.goal.condition == "Make tests pass"
    assert loop.goal.max_iterations == 20

    loop.set_goal("Different goal", max_iterations=5)
    assert loop.goal.condition == "Different goal"
    assert loop.goal.max_iterations == 5

    loop.clear_goal()
    assert loop.goal is None


def test_clear_goal_is_idempotent():
    from drydock.core.agent_loop import AgentLoop
    loop = MagicMock(spec=AgentLoop)
    loop.clear_goal = AgentLoop.clear_goal.__get__(loop)
    loop.goal = None
    loop.clear_goal()  # should not raise
    assert loop.goal is None

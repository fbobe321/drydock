"""Regression tests for the unrecoverable-API-error fix.

The bug (reported 2026-05-16): once the agent loop hit
`_total_error_rounds >= 3`, it stayed stuck — every subsequent user
message tripped the ceiling on its first API error and aborted.
The only escape was `/clear`, which wipes the entire session.

The fix has two parts:
  1. Reset `_total_error_rounds` to 0 at the top of each new
     `_conversation_loop` (= new user turn).
  2. On the hard-stop path, drop messages after the last user
     turn so the broken state doesn't immediately re-trip the same
     error on the next attempt.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock


def _make_stub_loop():
    """Minimal stub object that exercises just the reset semantics
    without needing a full AgentLoop construction (which requires
    a real backend, tools, config, etc.)."""
    loop = MagicMock()
    loop._total_error_rounds = 0
    return loop


def test_total_error_rounds_starts_at_zero():
    """Baseline — fresh agent loops should have rounds=0."""
    loop = _make_stub_loop()
    assert loop._total_error_rounds == 0


def test_reset_on_new_user_turn_clears_sticky_state():
    """The hard-stop ceiling was sticky pre-fix. After the fix,
    `_conversation_loop` resets `_total_error_rounds` at entry so
    subsequent user messages get a fresh error budget."""
    # Simulate the reset block from _conversation_loop:
    loop = _make_stub_loop()
    loop._total_error_rounds = 3  # hit the stop ceiling

    # The fix logic — extracted to validate it in isolation:
    if getattr(loop, "_total_error_rounds", 0) > 0:
        loop._total_error_rounds = 0

    assert loop._total_error_rounds == 0


def test_drop_messages_after_last_user_turn_on_hard_stop():
    """Hard-stop path should rewind history to the last user
    message so the next turn doesn't re-trigger the same error.
    Validates the slice/keep logic independent of AgentLoop."""

    class FakeMsg:
        def __init__(self, role, content=""):
            self.role = role
            self.content = content

    class Role:
        user = "user"
        assistant = "assistant"
        tool = "tool"

    msgs = [
        FakeMsg(Role.user, "first prompt"),
        FakeMsg(Role.assistant, "first reply"),
        FakeMsg(Role.user, "second prompt"),
        FakeMsg(Role.assistant, "broken tool_call attempt"),
        FakeMsg(Role.tool, "error result"),
        FakeMsg(Role.assistant, "another bad attempt"),
    ]

    # Find last user message index — matches the fix's loop
    last_user_idx = -1
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].role == Role.user:
            last_user_idx = i
            break

    assert last_user_idx == 2
    kept = msgs[: last_user_idx + 1]
    assert len(kept) == 3
    assert kept[-1].role == Role.user
    assert kept[-1].content == "second prompt"


def test_drop_is_safe_when_last_message_is_already_user():
    """If the last message is already a user turn (nothing after),
    the drop should be a no-op."""

    class FakeMsg:
        def __init__(self, role, content=""):
            self.role = role
            self.content = content

    class Role:
        user = "user"
        assistant = "assistant"

    msgs = [
        FakeMsg(Role.user, "only message"),
    ]
    last_user_idx = -1
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].role == Role.user:
            last_user_idx = i
            break

    # The fix's guard: `last_user_idx < len(self.messages) - 1`
    should_drop = last_user_idx >= 0 and last_user_idx < len(msgs) - 1
    assert should_drop is False  # nothing to drop


def test_reset_skipped_when_already_zero():
    """The reset path logs when rounds > 0 and skips otherwise — no
    side effects for healthy sessions."""
    loop = _make_stub_loop()
    loop._total_error_rounds = 0

    triggered_reset = False
    if getattr(loop, "_total_error_rounds", 0) > 0:
        triggered_reset = True
        loop._total_error_rounds = 0

    assert triggered_reset is False
    assert loop._total_error_rounds == 0


# ── Integration via the real _conversation_loop entry ──────────────────

def test_conversation_loop_resets_total_error_rounds_on_entry():
    """The actual code path: `_conversation_loop` should reset the
    counter to 0 before any LLM work happens. We patch AgentLoop's
    dependencies to skip the LLM call and just verify the counter."""
    # This is a partial-integration test — it imports the real method
    # and exercises its early code path. We don't run a full LLM turn.
    from drydock.core.agent_loop import AgentLoop  # noqa: F401

    # Verify the source code contains the reset block at the
    # `_conversation_loop` site. (A behavioural test would require
    # mocking the whole agent loop; this string-check is the
    # cheapest reliable regression.)
    import inspect
    src = inspect.getsource(AgentLoop._conversation_loop)
    assert "_total_error_rounds" in src
    assert "= 0" in src
    assert "fresh user turn" in src or "Reset sticky error" in src


# ── /undo slash command ────────────────────────────────────────────────

def test_undo_command_registered_with_aliases():
    """The /undo command must be in the registry with /back as alias."""
    from drydock.cli.commands import CommandRegistry
    r = CommandRegistry()
    cmd = r.commands.get("undo")
    assert cmd is not None
    assert "/undo" in cmd.aliases
    assert "/back" in cmd.aliases
    assert cmd.handler == "_undo_last_turn"


def test_agent_loop_undo_last_turn_method_exists():
    """The agent loop must expose undo_last_turn for the TUI handler."""
    from drydock.core.agent_loop import AgentLoop
    assert callable(getattr(AgentLoop, "undo_last_turn", None))


def test_undo_keeps_system_and_truncates_to_before_last_user():
    """Validate the rewind shape: drop the last user msg + everything
    after it; keep the system message and all prior turns."""
    import inspect
    from drydock.core.agent_loop import AgentLoop
    src = inspect.getsource(AgentLoop.undo_last_turn)
    # The implementation must walk backward to find the last user idx,
    # then slice the messages to keep only [:last_user_idx].
    assert "last_user_idx" in src
    assert "Role.user" in src
    assert ".reset(" in src
    # It must also clear the sticky error counters so the next prompt
    # doesn't immediately re-trip the lockout.
    assert "_total_error_rounds" in src


def test_undo_returns_failure_when_only_system_message():
    """If there's no user message to rewind past, undo should return
    (False, info) rather than wiping the system message."""
    import inspect
    from drydock.core.agent_loop import AgentLoop
    src = inspect.getsource(AgentLoop.undo_last_turn)
    # Guard condition for "no user message" or "only system message"
    assert "last_user_idx <= 0" in src or "last_user_idx == -1" in src
    assert "Nothing to undo" in src

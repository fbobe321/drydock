"""The /compact slash command (advertised in /help) must actually compact:
wire it to compaction.compact and report tokens freed. Regression for the gap
where /help listed /compact but handle_command never implemented it."""
from __future__ import annotations

from drydock import cli
from drydock.agent import AgentState
from drydock.compaction import estimate_tokens


def _state_with_big_tool_result() -> AgentState:
    state = AgentState()
    state.messages = [
        {"role": "user", "content": "do a thing"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "name": "Bash", "input": {"command": "cat big"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "Z" * 8000},
        {"role": "assistant", "content": "done"},
    ]
    return state


def test_compact_command_is_handled():
    """/compact returns True (handled), not False (falls through to the agent)."""
    state = AgentState()
    assert cli.handle_command("/compact", state, {"context_limit": 65536}) is True


def test_compact_shrinks_a_bloated_conversation(capsys):
    state = _state_with_big_tool_result()
    before = estimate_tokens(state.messages)

    cli.handle_command("/compact", state, {"context_limit": 65536})

    after = estimate_tokens(state.messages)
    assert after < before  # the 8000-char tool result was truncated
    out = capsys.readouterr().out.lower()
    assert "compact" in out and "freed" in out


def test_compact_on_empty_conversation_is_a_noop(capsys):
    state = AgentState()
    assert cli.handle_command("/compact", state, {"context_limit": 65536}) is True
    assert "nothing to compact" in capsys.readouterr().out.lower()

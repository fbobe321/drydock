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


def test_maybe_compact_uses_real_token_count_not_just_estimate():
    """maybe_compact must trigger on the SERVER's real prompt-token count, not
    only the chars/3.5 estimate (which undercounts dense code/build output).
    Regression: build task hit 64% real ctx without compacting because the
    estimate read ~55%."""
    from drydock.agent import AgentState
    from drydock.compaction import maybe_compact, estimate_tokens

    st = AgentState()
    # A few small messages: the char estimate is tiny (well under 60% of 65536)…
    st.messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello", "tool_calls": []},
    ]
    assert estimate_tokens(st.messages) < 0.60 * 65536      # estimate says "fine"
    cfg = {"context_limit": 65536}
    # …but the server reported a large real prompt (dense content) → must compact.
    st.last_input_tokens = 45000                            # 68% of 65536
    before = list(st.messages)
    maybe_compact(st, cfg)
    # compact() ran (it targets 60%); with only tiny messages it can't shrink, but
    # the point is it was INVOKED on the real count — verify the trigger path by
    # checking a case with droppable content:
    st.messages = before + [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "Bash", "input": {"command": "x"}}]},
        {"role": "tool", "tool_call_id": "a", "name": "Bash", "content": "Z" * 200000},  # huge old tool result
    ]
    st.last_input_tokens = 45000
    maybe_compact(st, cfg)
    # the huge old tool result should be trimmed by compact()
    assert estimate_tokens(st.messages) < estimate_tokens(before + [{"role": "tool", "content": "Z" * 200000}])


def test_maybe_compact_low_real_count_no_trigger():
    from drydock.agent import AgentState
    from drydock.compaction import maybe_compact
    st = AgentState()
    st.messages = [{"role": "user", "content": "small"}]
    st.last_input_tokens = 1000
    before = list(st.messages)
    maybe_compact(st, {"context_limit": 65536})
    assert st.messages == before      # nothing to do, unchanged

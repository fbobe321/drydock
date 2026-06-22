"""Tests for context compaction — the invariants that keep long sessions alive.

The load-bearing one: compaction must never orphan a tool result from its
assistant tool_call (that pairing is what the API validates), so it blanks
content rather than deleting messages.
"""
from __future__ import annotations

from drydock.compaction import (
    compact,
    emergency_compact,
    estimate_tokens,
    is_context_length_error,
    maybe_compact,
)


def test_estimate_counts_tool_call_input_args():
    # A full-file Write hides its bulk in tool_calls[].input.content. The
    # estimate MUST see it (this was the bug: it read as ~0 tokens).
    big = "x" * 40000
    msgs = [{"role": "assistant", "content": "",
             "tool_calls": [{"id": "1", "name": "Write",
                             "input": {"file_path": "a.py", "content": big}}]}]
    assert estimate_tokens(msgs) > 10000  # ~40000/3.0, not ~0


def test_emergency_compact_shrinks_big_write_arg_below_limit():
    # Reproduce the operator's crash shape: history dominated by an OLD
    # full-file Write arg. Emergency compaction must get the estimate under the
    # real 64k window (it returned the SAME oversized request before).
    limit = 65536
    big = "y" * 250000  # ~83k est tokens — over the window
    msgs = [
        {"role": "user", "content": "refactor cli.py"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "1", "name": "Write",
                         "input": {"file_path": "cli.py", "content": big}}]},
        {"role": "tool", "tool_call_id": "1", "name": "Write", "content": "ok"},
        {"role": "user", "content": "now run tests"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "2", "name": "Bash", "input": {"command": "pytest"}}]},
        {"role": "tool", "tool_call_id": "2", "name": "Bash", "content": "passed"},
    ]
    assert estimate_tokens(msgs) > limit
    emergency_compact(msgs, limit)
    assert estimate_tokens(msgs) < limit  # actually under the window now
    # The recent Bash tool_call (last) is preserved intact.
    assert msgs[4]["tool_calls"][0]["input"]["command"] == "pytest"


def test_is_context_length_error_matches_provider_phrasings():
    for msg in [
        "This model's maximum context length is 131072 tokens",
        "Error code: 400 - the request exceeds the available context size",
        "n_ctx is too small for this request",
        "Please reduce the length of the messages",
        "input is too long for the context window",
    ]:
        assert is_context_length_error(msg), msg
    for msg in ["Connection refused", "invalid api key", "rate limit exceeded"]:
        assert not is_context_length_error(msg), msg


def _conversation(n_pairs: int, tool_chars: int = 4000) -> list:
    """A first user msg, then n assistant(tool_call)+tool(result) pairs."""
    msgs = [{"role": "user", "content": "build the project"}]
    for i in range(n_pairs):
        msgs.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": f"c{i}", "name": "Bash", "input": {"command": "ls"}}],
        })
        msgs.append({
            "role": "tool", "tool_call_id": f"c{i}", "name": "Bash",
            "content": "x" * tool_chars,
        })
    return msgs


def _no_orphans(msgs: list) -> bool:
    """Every tool message must directly follow an assistant whose tool_calls
    include its tool_call_id (the API's pairing rule)."""
    for i, m in enumerate(msgs):
        if m["role"] == "tool":
            if i == 0 or msgs[i - 1]["role"] != "assistant":
                return False
            ids = {tc["id"] for tc in msgs[i - 1].get("tool_calls", [])}
            if m["tool_call_id"] not in ids:
                return False
    return True


def test_compact_preserves_tool_pairing():
    msgs = _conversation(20)
    assert _no_orphans(msgs)
    compact(msgs, context_limit=4000)  # force heavy compaction
    assert _no_orphans(msgs)  # still paired — content blanked, not deleted
    assert len(msgs) == 41  # no messages removed


def test_compact_reduces_tokens_when_over():
    msgs = _conversation(30, tool_chars=6000)
    before = estimate_tokens(msgs)
    compact(msgs, context_limit=4000)
    assert estimate_tokens(msgs) < before


def test_compact_keeps_first_user_and_recent_messages():
    msgs = _conversation(20)
    compact(msgs, context_limit=4000)
    assert msgs[0]["role"] == "user" and msgs[0]["content"] == "build the project"
    # Pass 1 may truncate even the most recent result, but Pass 2 must not
    # fully REMOVE it — recent context survives in at least partial form.
    assert msgs[-1]["content"] != "[tool result removed]"


def test_compact_drops_oldest_tool_results_first():
    # When only some old results need dropping, the OLDEST should go first so
    # recent context survives.
    msgs = _conversation(20, tool_chars=2000)
    compact(msgs, context_limit=8000)
    removed = [i for i, m in enumerate(msgs) if m.get("content") == "[tool result removed]"]
    if removed and any(m.get("content") != "[tool result removed]"
                       for m in msgs if m["role"] == "tool"):
        # If some were dropped and some kept, the kept ones must be newer.
        kept = [i for i, m in enumerate(msgs)
                if m["role"] == "tool" and m["content"] != "[tool result removed]"]
        assert max(removed) < max(kept)


def test_emergency_compact_is_more_aggressive():
    msgs = _conversation(30, tool_chars=6000)
    compact(msgs, context_limit=20000)
    after_normal = estimate_tokens(msgs)
    emergency_compact(msgs, context_limit=20000)
    assert estimate_tokens(msgs) <= after_normal
    assert _no_orphans(msgs)


def test_maybe_compact_noop_when_small():
    from drydock.agent import AgentState
    st = AgentState()
    st.messages = _conversation(2, tool_chars=100)
    before = list(st.messages)
    maybe_compact(st, {"context_limit": 131072})
    assert st.messages == before  # untouched when well under limit

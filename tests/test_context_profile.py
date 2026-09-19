"""Context composition (drydock/context_profile.py) — measure where a large window's
tokens actually go, instead of assuming they are tool results."""
from drydock.context_profile import compose, render, tool_call_arg_tokens


def test_tool_call_args_are_counted():
    """A Write carries a whole file body in tool_calls[].input, invisible to a naive
    per-role content breakdown."""
    m = {"role": "assistant", "content": "writing the file",
         "tool_calls": [{"id": "1", "name": "Write",
                         "input": {"path": "a.py", "content": "x" * 9000}}]}
    assert tool_call_arg_tokens(m) > 2900
    assert tool_call_arg_tokens({"role": "user", "content": "hi"}) == 0


def test_compose_breaks_down_by_role():
    msgs = [{"role": "user", "content": "u" * 300},
            {"role": "assistant", "content": "a" * 600},
            {"role": "tool", "content": "t" * 900}]
    p = compose(msgs, system="s" * 1500)
    assert p["by_role"]["user"] == 100
    assert p["by_role"]["assistant"] == 200
    assert p["by_role"]["tool"] == 300
    assert p["system_tokens"] == 500
    assert p["total_tokens"] == 1100
    assert p["n_messages"] == 3


def test_pageable_vs_unpageable_split():
    """The number that matters: how much of the window a tool-result pager could even
    touch. On the observed live run this was near zero."""
    msgs = [{"role": "assistant", "content": "a" * 3000},
            {"role": "tool", "content": "t" * 300}]
    p = compose(msgs, system="s" * 3000)
    assert p["pageable_tool_tokens"] == 100
    assert p["unpageable_tokens"] == p["total_tokens"] - 100
    assert p["unpageable_tokens"] > p["pageable_tool_tokens"] * 10


def test_largest_contributors_are_ranked():
    msgs = [{"role": "user", "content": "small"},
            {"role": "tool", "content": "B" * 6000},
            {"role": "tool", "content": "A" * 3000}]
    p = compose(msgs)
    assert p["largest"][0]["index"] == 1 and p["largest"][1]["index"] == 2
    assert p["largest"][0]["tokens"] == 2000


def test_render_is_readable_and_totals_to_100():
    p = compose([{"role": "tool", "content": "t" * 3000}], system="s" * 3000)
    out = render(p)
    assert "context:" in out and "system (pinned)" in out and "pageable as tool results" in out


def test_empty_context():
    p = compose([], system="")
    assert p["total_tokens"] == 0 and p["n_messages"] == 0
    assert render(p).startswith("context: 0 tokens")

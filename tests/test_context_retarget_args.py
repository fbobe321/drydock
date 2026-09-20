"""RE-TARGET SPEC (part 2) — tool-call ARGUMENTS (8% of the window).

Measured composition of a real 14,126-token window (validation PRD §0):

    assistant turns      11,577   82%
    system prompt         1,755   12%   (pinned, unpageable)
    tool-call arguments   1,118    8%
    tool results            767    5%   <- the only thing MCR currently pages

These tests define paging over the other 90%. They are expected to FAIL until
context_window is re-targeted; they are the verifier for that work.
"""
import pytest

from drydock.compaction import estimate_tokens
from drydock.context_runtime import ContextStore
from drydock.context_window import L_FULL, assemble, modularize

BIG = 2500   # chars per heavy message


def _assistant_heavy(n=8):
    """A transcript dominated by the model's own turns — the real shape."""
    msgs = [{"role": "user", "content": "fix the bugs"}]
    for i in range(n):
        msgs.append({"role": "assistant", "content": f"REASONING {i} " * (BIG // 12)})
        msgs.append({"role": "tool", "content": f"ok {i}"})          # tiny, as in reality
    return msgs


def _write_heavy(n=6):
    """Tool-call arguments carrying whole file bodies."""
    msgs = [{"role": "user", "content": "write the files"}]
    for i in range(n):
        msgs.append({"role": "assistant", "content": f"writing {i}",
                     "tool_calls": [{"id": str(i), "name": "Write",
                                     "input": {"path": f"f{i}.py",
                                               "content": f"BODY{i}\n" * 400}}]})
        msgs.append({"role": "tool", "content": "written"})
    return msgs


@pytest.mark.xfail(reason="NOT YET IMPLEMENTED: tool-call arguments are ~8% of a "
                          "real window and modularize() still only reads m['content']. "
                          "Kept as an executable spec; flips to XPASS when built.",
                   strict=False)
def test_tool_call_arguments_are_pageable(tmp_path):
    """A Write carries a whole file body in tool_calls[].input — 8% of the window and
    invisible to a per-role content breakdown."""
    s = ContextStore(root=str(tmp_path), name="rt4")
    msgs = _write_heavy()
    before = estimate_tokens(msgs)
    out, rep = assemble(msgs, s, budget=int(before * 0.5))
    assert rep["downgraded"], "tool-call arguments must be pageable"
    assert estimate_tokens(out) < before



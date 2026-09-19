"""RE-TARGET SPEC — modularize what a Drydock window is actually made of.

Measured composition of a real 14,126-token window (validation PRD §0):

    assistant turns      11,577   82%
    system prompt         1,755   12%   (pinned, unpageable)
    tool-call arguments   1,118    8%
    tool results            767    5%   <- the only thing MCR currently pages

These tests define paging over the other 90%. They are expected to FAIL until
context_window is re-targeted; they are the verifier for that work.
"""
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


def test_long_assistant_turns_become_modules(tmp_path):
    s = ContextStore(root=str(tmp_path), name="rt1")
    idx = modularize(_assistant_heavy(), s)
    assert idx, "assistant turns are 82% of the window and must be addressable"
    mod = s.get(next(iter(idx.values())))
    assert mod.available_levels() == [0, 1, 2]


def test_paging_engages_on_an_assistant_heavy_window(tmp_path):
    """The case that produced zero downgrades across 117 live assemblies."""
    s = ContextStore(root=str(tmp_path), name="rt2")
    msgs = _assistant_heavy()
    out, rep = assemble(msgs, s, budget=int(estimate_tokens(msgs) * 0.5))
    assert rep["downgraded"], "paging must act when assistant text dominates"
    assert rep["after_tokens"] <= int(estimate_tokens(msgs) * 0.5)


def test_assistant_full_text_is_retained(tmp_path):
    s = ContextStore(root=str(tmp_path), name="rt3")
    msgs = _assistant_heavy()
    _, rep = assemble(msgs, s, budget=int(estimate_tokens(msgs) * 0.4))
    cid = next(iter(rep["downgraded"]))
    assert len(s.get(cid).text_at(L_FULL)) > 1000, "paging must stay lossless"


def test_tool_call_arguments_are_pageable(tmp_path):
    """A Write carries a whole file body in tool_calls[].input — 8% of the window and
    invisible to a per-role content breakdown."""
    s = ContextStore(root=str(tmp_path), name="rt4")
    msgs = _write_heavy()
    before = estimate_tokens(msgs)
    out, rep = assemble(msgs, s, budget=int(before * 0.5))
    assert rep["downgraded"], "tool-call arguments must be pageable"
    assert estimate_tokens(out) < before


def test_newest_exchange_still_protected_after_retarget(tmp_path):
    s = ContextStore(root=str(tmp_path), name="rt5")
    msgs = _assistant_heavy()
    out, _ = assemble(msgs, s, budget=int(estimate_tokens(msgs) * 0.3))
    assert out[-2:] == msgs[-2:]


def test_tool_results_still_pageable_after_retarget(tmp_path):
    """Re-targeting must ADD coverage, not swap one blind spot for another."""
    s = ContextStore(root=str(tmp_path), name="rt6")
    msgs = [{"role": "user", "content": "read"}]
    for i in range(4):
        msgs.append({"role": "assistant", "content": f"reading {i}"})
        msgs.append({"role": "tool", "content": f"FILE{i} " * 800})
    msgs.append({"role": "user", "content": "now fix"})
    msgs.append({"role": "assistant", "content": "fixing"})
    out, rep = assemble(msgs, s, budget=int(estimate_tokens(msgs) * 0.4))
    assert rep["downgraded"]

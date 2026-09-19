"""Modular context window (drydock/context_window.py) — the part of MCR that actually
decides what the model sees: tool results become addressable modules with resolutions,
and the prompt is ASSEMBLED under a budget rather than replayed or scribbled out."""
from drydock.compaction import estimate_tokens
from drydock.context_window import (
    L_FULL,
    L_POINTER,
    L_SUMMARY,
    assemble,
    modularize,
    tool_module_id,
)
from drydock.context_runtime import ContextStore


def convo(n_tools=12, chars=6000, keep_small=False):
    m = [{"role": "user", "content": "do the thing"}]
    for i in range(n_tools):
        m.append({"role": "assistant", "content": f"calling tool {i}"})
        body = f"OUTPUT{i} " * (chars // 9) if not keep_small else "tiny"
        m.append({"role": "tool", "content": body})
    return m


def test_modularize_registers_substantial_tool_results(tmp_path):
    s = ContextStore(root=str(tmp_path), name="m1")
    msgs = convo(3)
    idx = modularize(msgs, s)
    assert len(idx) == 3
    mod = s.get(tool_module_id(2))
    assert mod is not None and mod.available_levels() == [0, 1, 2]
    assert mod.level == L_FULL


def test_small_tool_results_are_not_modularized(tmp_path):
    s = ContextStore(root=str(tmp_path), name="m2")
    assert modularize(convo(3, keep_small=True), s) == {}


def test_modularize_is_idempotent(tmp_path):
    s = ContextStore(root=str(tmp_path), name="m3")
    msgs = convo(3)
    modularize(msgs, s)
    v1 = s.get(tool_module_id(2)).version
    modularize(msgs, s)
    assert s.get(tool_module_id(2)).version == v1     # unchanged content -> no churn


def test_under_budget_is_returned_unchanged(tmp_path):
    s = ContextStore(root=str(tmp_path), name="a1")
    msgs = convo(2)
    out, rep = assemble(msgs, s, budget=10_000_000)
    assert out == msgs and rep["first_changed_index"] is None


def test_assembly_fits_the_budget_by_degrading(tmp_path):
    s = ContextStore(root=str(tmp_path), name="a2")
    msgs = convo(12)
    budget = int(estimate_tokens(msgs) * 0.5)
    out, rep = assemble(msgs, s, budget=budget)
    assert rep["after_tokens"] <= budget
    assert rep["downgraded"]                      # it degraded rather than deleting


def test_nothing_is_lost_full_text_stays_retrievable(tmp_path):
    """The difference from compaction: paged-out content is still there."""
    s = ContextStore(root=str(tmp_path), name="a3")
    msgs = convo(12)
    original = msgs[-1]["content"]
    out, rep = assemble(msgs, s, budget=int(estimate_tokens(msgs) * 0.4))
    cid = next(iter(rep["downgraded"]))
    assert s.get(cid).text_at(L_FULL) in [m["content"] for m in msgs]  # store has full text
    assert msgs[-1]["content"] == original        # caller's list never mutated


def test_recent_window_is_never_degraded(tmp_path):
    s = ContextStore(root=str(tmp_path), name="a4")
    msgs = convo(12)
    out, _ = assemble(msgs, s, budget=int(estimate_tokens(msgs) * 0.3), keep_last=8)
    assert out[-8:] == msgs[-8:]


def test_degrades_latest_first_so_the_prefix_survives(tmp_path):
    s = ContextStore(root=str(tmp_path), name="a5")
    msgs = convo(12)
    out, rep = assemble(msgs, s, budget=int(estimate_tokens(msgs) * 0.75))
    # the earliest messages must be untouched
    i = rep["first_changed_index"]
    assert i is not None and i > len(msgs) // 3
    assert out[:i] == msgs[:i]


def test_resolutions_are_monotone_across_turns(tmp_path):
    """A settled downgrade stays down, so the prompt stops churning and the cached
    prefix is stable turn to turn."""
    s = ContextStore(root=str(tmp_path), name="a6")
    msgs = convo(12)
    _, rep1 = assemble(msgs, s, budget=int(estimate_tokens(msgs) * 0.5))
    cid, level = next(iter(rep1["downgraded"].items()))
    # a later turn with a generous budget must NOT silently restore full resolution
    out2, rep2 = assemble(msgs, s, budget=10_000_000)
    assert s.get(cid).level == level
    assert rep2["downgraded"] == {} or rep2["downgraded"].get(cid, level) <= level


def test_pointer_level_is_tiny_but_addressable(tmp_path):
    s = ContextStore(root=str(tmp_path), name="a7")
    msgs = convo(12)
    modularize(msgs, s)
    mod = s.get(tool_module_id(2))
    ptr = mod.text_at(L_POINTER)
    assert tool_module_id(2) in ptr and len(ptr) < 200
    assert mod.text_at(L_SUMMARY) != mod.text_at(L_FULL)

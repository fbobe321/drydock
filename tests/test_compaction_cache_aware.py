"""Cache-aware compaction (drydock/compaction.compact_cache_aware) — reclaim from the
LATEST eligible messages so the cached prefix survives. See cache-aware MCR spec §24 and
MCR PRD Appendix A.1 (a head edit costs ~0.98x a cold prefill, a tail edit ~0.24x)."""
import copy

from drydock.compaction import (
    compact,
    compact_cache_aware,
    estimate_tokens,
    first_divergence_index,
)


def _convo(n_tools=12, tool_chars=6000):
    msgs = [{"role": "user", "content": "do the thing"}]
    for i in range(n_tools):
        msgs.append({"role": "assistant", "content": f"calling tool {i}"})
        msgs.append({"role": "tool", "content": f"OUTPUT{i} " * (tool_chars // 9)})
    return msgs


def test_first_divergence_index():
    a = [{"x": 1}, {"y": 2}, {"z": 3}]
    assert first_divergence_index(a, a) == 3
    b = copy.deepcopy(a); b[1] = {"y": 99}
    assert first_divergence_index(a, b) == 1


def test_default_compaction_rewrites_the_head(tmp_path=None):
    """Baseline: the existing strategy touches the earliest tool result."""
    before = _convo()
    limit = estimate_tokens(before) * 2
    after = compact(copy.deepcopy(before), context_limit=limit, target_frac=0.45)
    assert first_divergence_index(before, after) <= 2


def test_cache_aware_leaves_the_head_untouched(tmp_path=None):
    before = _convo()
    limit = estimate_tokens(before) * 2
    after = compact_cache_aware(copy.deepcopy(before), context_limit=limit,
                                target_frac=0.45)
    div_aware = first_divergence_index(before, after)
    div_default = first_divergence_index(
        before, compact(copy.deepcopy(before), context_limit=limit, target_frac=0.45))
    assert div_aware > div_default            # diverges later => cheaper re-prefill
    assert before[:div_default + 1] == after[:div_default + 1]


def test_cache_aware_still_reaches_the_target():
    before = _convo()
    limit = estimate_tokens(before) * 2
    target = int(limit * 0.45)
    after = compact_cache_aware(copy.deepcopy(before), context_limit=limit,
                                target_frac=0.45)
    assert estimate_tokens(after) <= target


def test_cache_aware_preserves_the_recent_window():
    before = _convo()
    limit = estimate_tokens(before) * 2
    after = compact_cache_aware(copy.deepcopy(before), context_limit=limit,
                                target_frac=0.45, keep_last=8)
    assert before[-8:] == after[-8:]          # genuinely recent context untouched


def test_noop_when_already_under_target():
    msgs = [{"role": "user", "content": "hi"}]
    before = copy.deepcopy(msgs)
    assert compact_cache_aware(msgs, context_limit=100000) == before


def test_default_compact_behaviour_is_unchanged():
    """The new strategy is additive — the shipped path must be byte-identical."""
    a, b = _convo(), _convo()
    limit = estimate_tokens(a) * 2
    assert compact(a, context_limit=limit) == compact(b, context_limit=limit)

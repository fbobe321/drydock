"""MCR Phase 1 (drydock/context_runtime.py): addressable context objects, the
persistent last-write-wins store, residency classes, the §18 scope-promotion ladder,
and reference resolution. See docs/modular_context_runtime_prd.md §5/§6/§18/§28."""
import pytest

from drydock.context_runtime import (
    ARCHIVED,
    BRANCH,
    GLOBAL,
    PINNED,
    PRIVATE,
    PROJECT,
    TASK,
    TOMBSTONED,
    WORKING,
    ContextModule,
    ContextStore,
    estimate_body_tokens,
    valid_context_id,
)


# ── §5 address space ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("cid", [
    "ctx://system/core",
    "ctx://repo/architecture",
    "ctx://branch/parser-fix-03",
    "ctx://decision/parser.strategy",
    "ctx://tools",
])
def test_valid_context_ids(cid):
    assert valid_context_id(cid)


@pytest.mark.parametrize("bad", ["", None, "system/core", "http://x/y", "ctx://", "ctx://a//b"])
def test_invalid_context_ids(bad):
    assert not valid_context_id(bad)


def test_module_rejects_bad_id_residency_scope():
    with pytest.raises(ValueError):
        ContextModule(context_id="nope")
    with pytest.raises(ValueError):
        ContextModule(context_id="ctx://a/b", residency="floating")
    with pytest.raises(ValueError):
        ContextModule(context_id="ctx://a/b", scope="cosmic")


# ── token sizing agrees with the compactor ───────────────────────────────────

def test_token_size_uses_the_compactor_estimator():
    from drydock.compaction import estimate_tokens
    body = "def f():\n    return 1\n" * 50
    assert estimate_body_tokens(body) == estimate_tokens([{"content": body}])
    assert ContextModule(context_id="ctx://a/b", body=body).token_size > 0


def test_empty_body_sizes_zero():
    assert ContextModule(context_id="ctx://a/b").token_size == 0


# ── persistence: append-only, last-write-wins ────────────────────────────────

def test_put_and_get_roundtrip(tmp_path):
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://task/objective", body="solve it", type="task"))
    m = s.get("ctx://task/objective")
    assert m.body == "solve it" and m.type == "task" and m.version == 1


def test_versions_bump_and_last_write_wins(tmp_path):
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://a/b", body="v1"))
    s.put(ContextModule(context_id="ctx://a/b", body="v2"))
    m = s.get("ctx://a/b")
    assert m.body == "v2" and m.version == 2
    assert len(s.all()) == 1                    # one logical module, two log rows


def test_storage_is_lossless_across_residency_change(tmp_path):
    """§2 — evicting from context must not destroy the body."""
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://research/cli", body="lots of help output"))
    s.set_residency("ctx://research/cli", ARCHIVED)
    m = s.get("ctx://research/cli")
    assert m.residency == ARCHIVED and m.body == "lots of help output"


def test_set_residency_unknown_module_or_class(tmp_path):
    s = ContextStore(root=str(tmp_path))
    assert s.set_residency("ctx://missing/x", ARCHIVED) is None
    s.put(ContextModule(context_id="ctx://a/b"))
    assert s.set_residency("ctx://a/b", "nonsense") is None


def test_by_residency_and_scope(tmp_path):
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://system/core", residency=PINNED, scope=GLOBAL))
    s.put(ContextModule(context_id="ctx://task/now", residency=WORKING, scope=TASK))
    s.put(ContextModule(context_id="ctx://dead/one", residency=TOMBSTONED))
    assert [m.context_id for m in s.by_residency(PINNED)] == ["ctx://system/core"]
    assert len(s.by_residency(PINNED, WORKING)) == 2
    assert [m.context_id for m in s.by_scope(TASK)] == ["ctx://task/now"]


def test_total_tokens_by_residency(tmp_path):
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://a/pin", body="x" * 300, residency=PINNED))
    s.put(ContextModule(context_id="ctx://a/arc", body="y" * 3000, residency=ARCHIVED))
    assert s.total_tokens(PINNED) == 100
    assert s.total_tokens() > s.total_tokens(PINNED)   # archived counted only in the total


def test_read_tolerates_corrupt_line(tmp_path):
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://a/b", body="ok"))
    with s.path.open("a", encoding="utf-8") as f:
        f.write("{broken\n")
    assert s.get("ctx://a/b").body == "ok"


# ── §18 scope promotion ladder ───────────────────────────────────────────────

def test_promotion_requires_evidence_above_branch(tmp_path):
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://f/x", scope=PRIVATE, verified=False))
    assert s.promote("ctx://f/x", BRANCH) is not None          # unverified may reach BRANCH
    assert s.promote("ctx://f/x", PROJECT) is None             # …but not PROJECT
    assert s.get("ctx://f/x").scope == BRANCH                  # unchanged by the refusal


def test_verified_knowledge_promotes_up_the_ladder(tmp_path):
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://d/y", scope=PRIVATE, verified=True))
    assert s.promote("ctx://d/y", TASK) is not None
    assert s.promote("ctx://d/y", PROJECT) is not None
    assert s.get("ctx://d/y").scope == PROJECT


def test_promotion_never_demotes(tmp_path):
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://d/z", scope=PROJECT, verified=True))
    assert s.promote("ctx://d/z", TASK) is None
    assert s.promote("ctx://d/z", PROJECT) is None             # same scope is not a promotion


# ── §5 references ────────────────────────────────────────────────────────────

def test_resolve_returns_dependency_closure_deps_first(tmp_path):
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://repo/parser", body="parser"))
    s.put(ContextModule(context_id="ctx://repo/grammar", body="grammar"))
    s.put(ContextModule(context_id="ctx://branch/fix",
                        dependencies=["ctx://repo/parser", "ctx://repo/grammar"]))
    got = [m.context_id for m in s.resolve("ctx://branch/fix")]
    assert got == ["ctx://repo/parser", "ctx://repo/grammar", "ctx://branch/fix"]


def test_resolve_is_cycle_safe(tmp_path):
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://a/one", dependencies=["ctx://a/two"]))
    s.put(ContextModule(context_id="ctx://a/two", dependencies=["ctx://a/one"]))
    got = [m.context_id for m in s.resolve("ctx://a/one")]
    assert sorted(got) == ["ctx://a/one", "ctx://a/two"]


def test_resolve_skips_dangling_reference(tmp_path):
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://a/one", dependencies=["ctx://gone/x"]))
    assert [m.context_id for m in s.resolve("ctx://a/one")] == ["ctx://a/one"]


# ══════════════════════ Phase 2: paging / Context View ══════════════════════

from drydock.context_runtime import (  # noqa: E402
    SHARED,
    VIEW_ORDER,
    build_view,
    prefix_reuse,
)


def _m(store, cid, residency=WORKING, body="x" * 300, priority=0.0, **kw):
    return store.put(ContextModule(context_id=cid, residency=residency, body=body,
                                   priority=priority, **kw))


def test_mount_unmount_are_residency_changes_not_deletions(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://r/cli", body="help output")
    s.unmount("ctx://r/cli")
    assert s.get("ctx://r/cli").residency == ARCHIVED
    assert s.get("ctx://r/cli").body == "help output"     # §2 lossless
    s.mount("ctx://r/cli")
    assert s.get("ctx://r/cli").residency == WORKING


def test_archived_modules_are_never_in_the_view(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://a/live")
    _m(s, "ctx://a/gone", residency=ARCHIVED)
    assert build_view(s, budget=10_000).ids() == ["ctx://a/live"]


def test_view_is_ordered_most_stable_first(tmp_path):
    """The measured Appendix A.1 rule: pinned -> shared -> tombstoned -> working."""
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://w/now", residency=WORKING)
    _m(s, "ctx://t/dead", residency=TOMBSTONED)
    _m(s, "ctx://s/repo", residency=SHARED)
    _m(s, "ctx://p/sys", residency=PINNED)
    assert build_view(s, budget=10_000).ids() == [
        "ctx://p/sys", "ctx://s/repo", "ctx://t/dead", "ctx://w/now"]
    assert VIEW_ORDER == (PINNED, SHARED, TOMBSTONED, WORKING)


def test_budget_evicts_lowest_priority_first(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://a/hi", priority=0.9)      # 100 tok each
    _m(s, "ctx://a/mid", priority=0.5)
    _m(s, "ctx://a/lo", priority=0.1)
    v = build_view(s, budget=200)
    assert sorted(v.ids()) == ["ctx://a/hi", "ctx://a/mid"]
    assert v.evicted == ["ctx://a/lo"]
    assert v.total_tokens == 200 and not v.over_budget


def test_pinned_is_exempt_from_the_budget(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://p/sys", residency=PINNED, body="y" * 900)   # 300 tok
    _m(s, "ctx://w/now", residency=WORKING)
    v = build_view(s, budget=100)
    assert "ctx://p/sys" in v.ids()          # pinned always resident (§6)
    assert v.over_budget and v.evicted == ["ctx://w/now"]


def test_selection_prefers_cheaper_module_on_equal_priority(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://a/big", body="z" * 900, priority=0.5)   # 300 tok
    _m(s, "ctx://a/small", body="z" * 150, priority=0.5)  # 50 tok
    v = build_view(s, budget=100)
    assert v.ids() == ["ctx://a/small"]


def test_include_restricts_but_pinned_still_mounts(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://p/sys", residency=PINNED)
    _m(s, "ctx://a/one")
    _m(s, "ctx://a/two")
    v = build_view(s, budget=10_000, include=["ctx://a/two"])
    assert sorted(v.ids()) == ["ctx://a/two", "ctx://p/sys"]


def test_stable_prefix_tokens_counts_pinned_and_shared_only(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://p/sys", residency=PINNED)      # 100
    _m(s, "ctx://s/repo", residency=SHARED)     # 100
    _m(s, "ctx://w/now", residency=WORKING)     # 100
    v = build_view(s, budget=10_000)
    assert v.stable_prefix_tokens == 200 and v.total_tokens == 300


def test_render_joins_bodies_in_prompt_order(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://w/now", residency=WORKING, body="WORK")
    _m(s, "ctx://p/sys", residency=PINNED, body="SYS")
    assert build_view(s, budget=10_000).render() == "SYS\n\nWORK"


# ── prefix_reuse: the offline predictor of Appendix A.1 re-prefill cost ──────

def test_prefix_reuse_no_previous_view_is_full_reprefill(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://a/one")
    r = prefix_reuse(None, build_view(s, budget=10_000))
    assert r["reused_tokens"] == 0 and r["reuse_pct"] == 0.0


def test_prefix_reuse_identical_view_is_total(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://p/sys", residency=PINNED)
    _m(s, "ctx://w/now", residency=WORKING)
    v = build_view(s, budget=10_000)
    r = prefix_reuse(v, build_view(s, budget=10_000))
    assert r["reuse_pct"] == 100.0 and r["reprefill_tokens"] == 0


def test_tail_change_preserves_the_prefix(tmp_path):
    """Changing a WORKING module (the tail) must keep the pinned+shared prefix."""
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://p/sys", residency=PINNED)
    _m(s, "ctx://s/repo", residency=SHARED)
    before = build_view(s, budget=10_000)
    _m(s, "ctx://w/now", residency=WORKING)          # new tail module
    r = prefix_reuse(before, build_view(s, budget=10_000))
    assert r["reused_tokens"] == 200 and r["reprefill_tokens"] == 100


def test_head_change_destroys_the_whole_prefix(tmp_path):
    """Mutating a PINNED (head) module invalidates everything after it — the 0.98x
    case the probe measured."""
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://p/sys", residency=PINNED, body="A" * 300)
    _m(s, "ctx://s/repo", residency=SHARED)
    _m(s, "ctx://w/now", residency=WORKING)
    before = build_view(s, budget=10_000)
    _m(s, "ctx://p/sys", residency=PINNED, body="B" * 300)   # same id, new version
    r = prefix_reuse(before, build_view(s, budget=10_000))
    assert r["diverged_at"] == 0 and r["reused_tokens"] == 0
    assert r["reprefill_tokens"] == 300


def test_middle_change_reuses_only_up_to_it(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://p/sys", residency=PINNED)
    _m(s, "ctx://s/repo", residency=SHARED, body="A" * 300)
    _m(s, "ctx://w/now", residency=WORKING)
    before = build_view(s, budget=10_000)
    _m(s, "ctx://s/repo", residency=SHARED, body="B" * 300)
    r = prefix_reuse(before, build_view(s, budget=10_000))
    assert r["diverged_at"] == 1 and r["reused_tokens"] == 100

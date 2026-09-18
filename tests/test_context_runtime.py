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


# ══════════════════════ Phase 4: tombstones (§6, Appendix A.3) ══════════════════════

from drydock.context_runtime import (  # noqa: E402
    Tombstone,
    revive,
    tombstone,
    tombstone_id,
)


def test_tombstone_id_is_derived_and_valid():
    tid = tombstone_id("ctx://branch/parser-fix-03")
    assert tid == "ctx://tombstone/branch.parser-fix-03" and valid_context_id(tid)


def test_tombstone_renders_the_prd_shape():
    body = Tombstone(
        approach="Replace parser with regex implementation",
        result="Failed tests 14, 17, and 19.",
        reason="Nested syntax cannot be represented by the proposed regex.",
        revisit_if="Grammar requirements change or nested syntax is removed.",
        source="ctx://branch/parser-fix-03",
    ).render()
    assert "Approach: Replace parser with regex" in body
    assert "Revisit only if: Grammar requirements change" in body
    assert "Source: ctx://branch/parser-fix-03" in body


def test_tombstone_archives_the_full_body_and_shrinks_the_view(tmp_path):
    """§13: a fat dead branch becomes a small record, and the full trace survives."""
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://branch/regex", body="TRACE " * 4000)        # ~4000 tok
    fat = build_view(s, budget=100_000).total_tokens
    t = tombstone(s, "ctx://branch/regex", approach="regex parser",
                  result="failed 14,17,19", reason="nested syntax")
    lean = build_view(s, budget=100_000).total_tokens
    assert lean < fat / 10                                    # compact record replaced the branch
    assert s.get("ctx://branch/regex").residency == ARCHIVED
    assert s.get("ctx://branch/regex").body.startswith("TRACE ")   # §2 lossless
    assert t.residency == TOMBSTONED and t.created_from == "ctx://branch/regex"


def test_tombstone_of_unknown_module_is_none(tmp_path):
    s = ContextStore(root=str(tmp_path))
    assert tombstone(s, "ctx://nope/x", approach="a") is None


def test_unevidenced_tombstone_cannot_become_project_knowledge(tmp_path):
    """Appendix A.3 — a model-authored 'this failed' claim with no verifier evidence
    must not durably suppress the approach project-wide."""
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://branch/hunch", scope=PRIVATE)
    t = tombstone(s, "ctx://branch/hunch", approach="a hunch", reason="felt wrong")
    assert t.verified is False
    assert s.promote(t.context_id, BRANCH) is not None
    assert s.promote(t.context_id, PROJECT) is None


def test_evidenced_tombstone_is_verified_and_promotable(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://branch/regex", scope=PRIVATE)
    t = tombstone(s, "ctx://branch/regex", approach="regex parser",
                  evidence={"tests_failed": [14, 17, 19], "fitness": 0.63})
    assert t.verified is True
    assert "Evidence:" in t.body and "tests_failed" in t.body
    assert s.promote(t.context_id, PROJECT) is not None


def test_tombstone_inherits_scope_and_owner(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://branch/x", scope=BRANCH, owner="ratchet-run-182")
    t = tombstone(s, "ctx://branch/x", approach="a")
    assert t.scope == BRANCH and t.owner == "ratchet-run-182"


def test_revive_by_tombstone_id_restores_the_original(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://branch/regex", body="THE WORK")
    t = tombstone(s, "ctx://branch/regex", approach="regex parser")
    back = revive(s, t.context_id)
    assert back.context_id == "ctx://branch/regex" and back.residency == WORKING
    assert back.body == "THE WORK"
    assert s.get(t.context_id).residency == ARCHIVED       # record kept, stops suppressing


def test_revive_by_original_id_also_works(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://branch/regex", body="THE WORK")
    t = tombstone(s, "ctx://branch/regex", approach="regex parser")
    back = revive(s, "ctx://branch/regex")
    assert back.residency == WORKING
    assert s.get(t.context_id).residency == ARCHIVED


def test_revive_unknown_is_none(tmp_path):
    s = ContextStore(root=str(tmp_path))
    assert revive(s, "ctx://nope/x") is None


def test_tombstone_sits_before_working_in_the_view(tmp_path):
    """Tombstones change less often than the current working set, so they belong
    earlier in the prompt (keeps volatile churn in the tail)."""
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://w/now", residency=WORKING)
    _m(s, "ctx://branch/dead")
    tombstone(s, "ctx://branch/dead", approach="dead end")
    ids = build_view(s, budget=100_000).ids()
    assert ids.index("ctx://tombstone/branch.dead") < ids.index("ctx://w/now")


# ═════════════ Phase 3: Ratchet integration (§10 txn, §11 checkpoint, §12 invariant) ═════════════

import pytest as _pytest  # noqa: E402

from drydock.context_runtime import (  # noqa: E402
    Conflict,
    ContextCheckpoint,
    commit_knowledge,
    conflict_id,
    context_transaction,
)


# ── §11 ContextCheckpoint mirrors GitCheckpoint ──────────────────────────────

def test_checkpoint_api_mirrors_gitcheckpoint(tmp_path):
    """A ratchet tooth should be able to checkpoint code and knowledge the same way."""
    from drydock.ratchet import GitCheckpoint
    s = ContextStore(root=str(tmp_path))
    cp = ContextCheckpoint(s)
    assert cp.available() is True
    for name in ("available", "snapshot", "restore"):
        assert hasattr(cp, name) and hasattr(GitCheckpoint, name)


def test_checkpoint_records_the_tooth_git_ref_and_fitness(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://a/one")
    cid = ContextCheckpoint(s).snapshot("tooth 14/22", git_ref="abc1234", fitness=0.636)
    rec = ContextCheckpoint(s).get(cid)
    assert rec["git_ref"] == "abc1234" and rec["fitness"] == 0.636
    assert rec["label"] == "tooth 14/22" and rec["versions"]["ctx://a/one"] == 1


def test_restore_reverts_a_changed_module(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://a/one", body="GOOD")
    cp = ContextCheckpoint(s)
    cid = cp.snapshot("before")
    _m(s, "ctx://a/one", body="REGRESSED")
    assert s.get("ctx://a/one").body == "REGRESSED"
    assert cp.restore(cid) is True
    assert s.get("ctx://a/one").body == "GOOD"


def test_restore_archives_modules_born_after_the_checkpoint(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://a/one")
    cp = ContextCheckpoint(s)
    cid = cp.snapshot()
    _m(s, "ctx://a/two")
    cp.restore(cid)
    assert s.get("ctx://a/two").residency == ARCHIVED     # evicted, not destroyed (§2)
    assert s.get("ctx://a/two").body != ""


def test_rollback_is_itself_lossless(tmp_path):
    """Restoring must append, never rewrite history — the regressed version stays."""
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://a/one", body="GOOD")
    cp = ContextCheckpoint(s)
    cid = cp.snapshot()
    _m(s, "ctx://a/one", body="REGRESSED")
    cp.restore(cid)
    assert s.get_version("ctx://a/one", 2).body == "REGRESSED"
    assert s.get("ctx://a/one").version == 3               # restore appended a new version


def test_restore_unknown_checkpoint_is_false(tmp_path):
    assert ContextCheckpoint(ContextStore(root=str(tmp_path))).restore("ckpt-9999") is False


# ── §12 the context-ratchet invariant ────────────────────────────────────────

def test_unverified_knowledge_is_overwritten_normally(tmp_path):
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://k/api", body="needs X", verified=False))
    stored, conflict = commit_knowledge(s, ContextModule(context_id="ctx://k/api",
                                                         body="does not need X"))
    assert conflict is None and stored.body == "does not need X"


def test_contradicting_verified_knowledge_raises_a_conflict(tmp_path):
    """§12 — the new observation must NOT silently replace a verified fact."""
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://k/api", body="API requires parameter X",
                        verified=True))
    stored, conflict = commit_knowledge(
        s, ContextModule(context_id="ctx://k/api", body="API appears not to require X"))
    assert stored is None and isinstance(conflict, Conflict)
    assert s.get("ctx://k/api").body == "API requires parameter X"   # old one stands
    c = s.get(conflict_id("ctx://k/api"))
    assert c is not None and c.residency == WORKING and c.verified is False
    assert "OLD (verified): API requires parameter X" in c.body
    assert "NEW (observed): API appears not to require X" in c.body


def test_identical_recommit_of_verified_knowledge_is_not_a_conflict(tmp_path):
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://k/api", body="requires X", verified=True))
    stored, conflict = commit_knowledge(s, ContextModule(context_id="ctx://k/api",
                                                         body="requires X"))
    assert conflict is None and stored is not None


def test_resolve_allows_the_overwrite_once_evidence_settles_it(tmp_path):
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://k/api", body="requires X", verified=True))
    stored, conflict = commit_knowledge(
        s, ContextModule(context_id="ctx://k/api", body="does not require X", verified=True),
        resolve=True)
    assert conflict is None and s.get("ctx://k/api").body == "does not require X"


# ── §10 transactional context changes ────────────────────────────────────────

def test_transaction_commit_keeps_the_work(tmp_path):
    s = ContextStore(root=str(tmp_path))
    with context_transaction(s, "try regex parser") as tx:
        _m(s, "ctx://branch/regex", body="WORK")
        tx.commit()
    assert s.get("ctx://branch/regex").residency == WORKING


def test_transaction_without_commit_rolls_back(tmp_path):
    s = ContextStore(root=str(tmp_path))
    with context_transaction(s) as tx:      # noqa: F841 — deliberately never committed
        _m(s, "ctx://branch/regex", body="SPECULATIVE")
    assert s.get("ctx://branch/regex").residency == ARCHIVED


def test_transaction_rolls_back_on_exception_and_reraises(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://a/one", body="GOOD")
    with _pytest.raises(RuntimeError):
        with context_transaction(s) as tx:
            _m(s, "ctx://a/one", body="CLOBBERED")
            tx.commit()                      # even a commit cannot survive the raise
            raise RuntimeError("boom")
    assert s.get("ctx://a/one").body == "GOOD"


def test_speculative_branch_cannot_contaminate_shared_knowledge(tmp_path):
    """The §10 point: an abandoned fork leaves shared knowledge untouched."""
    s = ContextStore(root=str(tmp_path))
    s.put(ContextModule(context_id="ctx://s/repo", body="TRUTH",
                        residency=SHARED, verified=True))
    with context_transaction(s) as tx:       # noqa: F841 — abandoned on purpose
        commit_knowledge(s, ContextModule(context_id="ctx://s/repo", body="HALLUCINATION"))
    assert s.get("ctx://s/repo").body == "TRUTH"


# ══════════════════ Phase 5: multi-resolution modules (§14) ══════════════════

L = {0: "ptr", 1: "a short summary " * 5, 2: "a detailed summary " * 40,
     3: "selected evidence " * 300}


def _multi(store, cid="ctx://repo/parser", **kw):
    return store.put(ContextModule(context_id=cid, resolutions=dict(L), **kw))


def test_defaults_to_the_most_detailed_level(tmp_path):
    s = ContextStore(root=str(tmp_path))
    m = _multi(s)
    assert m.available_levels() == [0, 1, 2, 3] and m.level == 3
    assert m.current_text == L[3]


def test_text_at_exact_and_degrades_to_nearest_lower(tmp_path):
    s = ContextStore(root=str(tmp_path))
    m = _multi(s)
    assert m.text_at(1) == L[1]
    assert m.text_at(9) == L[3]      # nothing above 3 → nearest below
    m2 = store_module_without(s, {0, 1})
    assert m2.text_at(1) == L[2]     # no 0/1 → smallest available (never upgrade past ask)


def store_module_without(store, drop):
    res = {k: v for k, v in L.items() if k not in drop}
    return store.put(ContextModule(context_id="ctx://repo/partial", resolutions=res))


def test_token_size_follows_the_selected_level(tmp_path):
    s = ContextStore(root=str(tmp_path))
    m = _multi(s)
    assert m.at_level(0).token_size < m.at_level(1).token_size < m.at_level(3).token_size


def test_at_level_is_a_copy_and_keeps_version(tmp_path):
    s = ContextStore(root=str(tmp_path))
    m = _multi(s)
    low = m.at_level(0)
    assert low.level == 0 and m.level == 3          # original untouched
    assert low.version == m.version and low.context_id == m.context_id


def test_resolutions_survive_the_store_roundtrip(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _multi(s)
    back = s.get("ctx://repo/parser")
    assert back.available_levels() == [0, 1, 2, 3] and back.level == 3
    assert back.current_text == L[3]


def test_view_degrades_instead_of_evicting(tmp_path):
    """§14 — a 20-token pointer still tells the model the thing exists; an eviction
    tells it nothing."""
    s = ContextStore(root=str(tmp_path))
    _multi(s, priority=0.5)
    v = build_view(s, budget=60)
    assert v.ids() == ["ctx://repo/parser"]          # kept, not evicted
    assert v.evicted == []
    assert v.degraded["ctx://repo/parser"] in (0, 1)
    assert v.total_tokens <= 60


def test_degrade_false_evicts_instead(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _multi(s, priority=0.5)
    v = build_view(s, budget=60, degrade=False)
    assert v.ids() == [] and v.evicted == ["ctx://repo/parser"]


def test_degrades_only_as_far_as_needed(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _multi(s, priority=0.5)
    full = ContextModule(context_id="ctx://x/y", resolutions=dict(L))
    v = build_view(s, budget=full.at_level(2).token_size + 5)
    assert v.degraded["ctx://repo/parser"] == 2      # not all the way down to 0


def test_module_without_resolutions_is_unaffected(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _m(s, "ctx://plain/one", body="z" * 3000, priority=0.5)
    v = build_view(s, budget=60)
    assert v.ids() == [] and v.evicted == ["ctx://plain/one"] and v.degraded == {}


def test_render_uses_the_selected_resolution(tmp_path):
    s = ContextStore(root=str(tmp_path))
    _multi(s, priority=0.5)
    assert build_view(s, budget=60).render() in (L[0], L[1])


def test_changing_resolution_breaks_the_prefix(tmp_path):
    """A same-id, same-version module at a DIFFERENT level is different text, so the
    cached prefix cannot survive it — prefix_reuse must not report reuse."""
    s = ContextStore(root=str(tmp_path))
    _multi(s, cid="ctx://repo/parser", priority=0.5, residency=SHARED)
    _m(s, "ctx://w/now", residency=WORKING, priority=0.1)
    big = build_view(s, budget=100_000)               # parser at L3
    small = build_view(s, budget=60)                  # parser degraded
    r = prefix_reuse(big, small)
    assert r["diverged_at"] == 0 and r["reused_tokens"] == 0

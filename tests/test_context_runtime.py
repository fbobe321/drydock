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

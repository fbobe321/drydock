"""Context replay and diff (drydock/context_replay.py) — validation PRD §29/§30/§31.
Reconstruct exactly what the model knew on a call, and what changed between two."""
from drydock.context_replay import (
    CallLog,
    diff,
    manifest_from_view,
    render_diff,
    replay,
)
from drydock.context_runtime import (
    PINNED,
    SHARED,
    WORKING,
    ContextModule,
    ContextStore,
    build_view,
)


def _store(tmp_path, name):
    s = ContextStore(root=str(tmp_path), name=name)
    s.put(ContextModule(context_id="ctx://system/core", body="KERNEL", residency=PINNED))
    s.put(ContextModule(context_id="ctx://repo/arch", body="PROJECT", residency=SHARED))
    s.put(ContextModule(context_id="ctx://tool/big", residency=WORKING,
                        resolutions={0: "ptr", 1: "summary text", 2: "FULL " * 400}))
    return s


def test_manifest_captures_the_view(tmp_path):
    s = _store(tmp_path, "m1")
    man = manifest_from_view(build_view(s, budget=100_000), call_id="1842", note="turn 3")
    assert man["call_id"] == "1842" and man["note"] == "turn 3"
    ids = [m["id"] for m in man["modules"]]
    assert ids[0] == "ctx://system/core"          # ordering preserved
    assert all("fingerprint" in m and "version" in m for m in man["modules"])


def test_call_log_roundtrip(tmp_path):
    s = _store(tmp_path, "m2")
    log = CallLog(s)
    log.record(manifest_from_view(build_view(s, budget=100_000), "1841"))
    log.record(manifest_from_view(build_view(s, budget=100_000), "1842"))
    assert [r["call_id"] for r in log.all()] == ["1841", "1842"]
    assert log.get("1842") is not None and log.get("9999") is None


def test_replay_reconstructs_what_the_model_saw(tmp_path):
    s = _store(tmp_path, "m3")
    view = build_view(s, budget=100_000)
    man = manifest_from_view(view, "1")
    assert replay(s, man) == view.render()


def test_replay_under_append_policy_shows_the_counterfactual(tmp_path):
    """§30: was this a model failure, or did MCR hand it a worse context?"""
    s = _store(tmp_path, "m4")
    tiny = build_view(s, budget=60)                       # forces the big module down
    man = manifest_from_view(tiny, "2")
    modular = replay(s, man, policy="modular")
    appended = replay(s, man, policy="append")
    assert len(appended) > len(modular)
    assert "FULL" in appended and "FULL" not in modular


def test_replay_flags_a_module_missing_from_the_store(tmp_path):
    s = _store(tmp_path, "m5")
    man = manifest_from_view(build_view(s, budget=100_000), "3")
    man["modules"].append({"id": "ctx://gone/x", "version": 1, "level": None, "tokens": 5})
    assert "MISSING FROM STORE" in replay(s, man)


# ── the diff, which is the actual debugging tool ────────────────────────────

def test_diff_of_identical_calls_is_fully_reusable(tmp_path):
    s = _store(tmp_path, "d1")
    man = manifest_from_view(build_view(s, budget=100_000), "a")
    d = diff(man, man)
    assert d["cache_invalidation_starts_at_token"] is None
    assert d["unchanged_prefix_modules"] == len(man["modules"])
    assert "prefix fully reusable" in render_diff(d)


def test_diff_detects_a_tail_addition_and_keeps_the_prefix(tmp_path):
    s = _store(tmp_path, "d2")
    before = manifest_from_view(build_view(s, budget=100_000), "a")
    s.put(ContextModule(context_id="ctx://scratch/new", body="NEW WORK", residency=WORKING))
    after = manifest_from_view(build_view(s, budget=100_000), "b")
    d = diff(before, after)
    assert [e["id"] for e in d["added"]] == ["ctx://scratch/new"]
    assert d["unchanged_prefix_modules"] == len(before["modules"])
    assert d["cache_invalidation_starts_at_token"] == d["unchanged_prefix_tokens"] + 1


def test_diff_reports_compaction_with_sizes(tmp_path):
    """The case that cost several live runs to see: a module dropped to a cheaper
    resolution, which is different bytes even at the same version."""
    s = _store(tmp_path, "d3")
    before = manifest_from_view(build_view(s, budget=100_000), "a")
    after = manifest_from_view(build_view(s, budget=60), "b")
    d = diff(before, after)
    comp = d["compacted"]
    assert comp and comp[0]["id"] == "ctx://tool/big"
    assert comp[0]["from_tokens"] > comp[0]["to_tokens"]
    assert "COMPACTED" in render_diff(d) and "→" in render_diff(d)


def test_diff_detects_a_head_change_and_invalidates_immediately(tmp_path):
    s = _store(tmp_path, "d4")
    before = manifest_from_view(build_view(s, budget=100_000), "a")
    s.put(ContextModule(context_id="ctx://system/core", body="KERNEL v2", residency=PINNED))
    after = manifest_from_view(build_view(s, budget=100_000), "b")
    d = diff(before, after)
    assert d["unchanged_prefix_tokens"] == 0
    assert d["cache_invalidation_starts_at_token"] == 1
    assert d["changed"] and d["changed"][0]["id"] == "ctx://system/core"


def test_diff_reports_removal(tmp_path):
    s = _store(tmp_path, "d5")
    before = manifest_from_view(build_view(s, budget=100_000), "a")
    s.unmount("ctx://tool/big")
    after = manifest_from_view(build_view(s, budget=100_000), "b")
    d = diff(before, after)
    assert [e["id"] for e in d["removed"]] == ["ctx://tool/big"]
    assert "REMOVED" in render_diff(d)

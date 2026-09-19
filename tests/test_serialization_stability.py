"""Gate 2 (validation PRD §6, §32): serialization must be byte-identical across repeated
builds, or every cache measurement downstream is meaningless.

Specifically hunts the nondeterminism sources the PRD names: dict ordering, timestamps,
UUIDs, whitespace, generated headers, metadata ordering, JSON formatting. Dynamic
metadata must not appear inside a supposedly cache-stable prefix."""
import re

from drydock.context_runtime import (
    PINNED,
    SHARED,
    WORKING,
    ContextModule,
    ContextStore,
    build_view,
    prefix_reuse,
)

_TIMESTAMPISH = re.compile(r"\b1[6-9]\d{8}(?:\.\d+)?\b")          # epoch seconds
_UUIDISH = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}\b", re.I)


def _kernel(tmp_path, name="stab"):
    s = ContextStore(root=str(tmp_path), name=name)
    s.put(ContextModule(context_id="ctx://system/core", body="KERNEL RULES\n" * 20,
                        residency=PINNED))
    s.put(ContextModule(context_id="ctx://repo/arch", body="PROJECT LAYOUT\n" * 40,
                        residency=SHARED))
    s.put(ContextModule(context_id="ctx://task/state", body="CURRENT STATE\n" * 10,
                        residency=WORKING))
    return s


def test_rendered_context_is_byte_identical_across_100_builds(tmp_path):
    s = _kernel(tmp_path)
    first = build_view(s, budget=100_000).render()
    for _ in range(99):
        assert build_view(s, budget=100_000).render() == first


def test_fingerprints_are_stable_across_100_builds(tmp_path):
    s = _kernel(tmp_path)
    first = [m.fingerprint for m in build_view(s, budget=100_000).modules]
    for _ in range(99):
        assert [m.fingerprint for m in build_view(s, budget=100_000).modules] == first


def test_module_order_is_stable_across_builds(tmp_path):
    """Ordering churn would invalidate the prefix even with identical content."""
    s = _kernel(tmp_path)
    first = build_view(s, budget=100_000).ids()
    for _ in range(50):
        assert build_view(s, budget=100_000).ids() == first


def test_no_timestamps_or_uuids_leak_into_the_rendered_prefix(tmp_path):
    """Modules carry a `ts` and a version; neither may reach the serialized text."""
    s = _kernel(tmp_path)
    text = build_view(s, budget=100_000).render()
    assert not _TIMESTAMPISH.search(text), "epoch timestamp leaked into the prefix"
    assert not _UUIDISH.search(text), "uuid leaked into the prefix"


def test_rewriting_a_module_with_identical_content_does_not_change_the_prefix(tmp_path):
    """Re-put with the same body bumps the version, but the BYTES are unchanged, so the
    prefix must still be reusable — cache identity follows serialization, not version."""
    s = _kernel(tmp_path)
    before = build_view(s, budget=100_000)
    s.put(ContextModule(context_id="ctx://repo/arch", body="PROJECT LAYOUT\n" * 40,
                        residency=SHARED))
    after = build_view(s, budget=100_000)
    assert after.render() == before.render()
    assert prefix_reuse(before, after)["reuse_pct"] == 100.0


def test_store_reload_produces_an_identical_view(tmp_path):
    """A fresh process reading the same log must serialize identically."""
    _kernel(tmp_path, "reload")
    a = build_view(ContextStore(root=str(tmp_path), name="reload"), budget=100_000).render()
    b = build_view(ContextStore(root=str(tmp_path), name="reload"), budget=100_000).render()
    assert a == b and a


def test_manifest_is_stable_apart_from_its_call_metadata(tmp_path):
    s = _kernel(tmp_path)
    m1 = build_view(s, budget=100_000).manifest()
    m2 = build_view(s, budget=100_000).manifest()
    assert m1 == m2                       # no ts/uuid inside the manifest itself

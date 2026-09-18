"""MCR prompt injection (drydock/context_inject.py) — the first path where the context
runtime changes what the model sees. Placement is dictated by the measured cache rule:
the packet rides at the TAIL so the ~95% stable prefix survives."""
from drydock.context_inject import (
    DEFAULT_PACKET_BUDGET,
    build_packet,
    enabled,
    inject,
    register,
    unregister,
)
from drydock.context_ratchet import ContextRatchet
from drydock.context_runtime import PINNED, SHARED, ContextModule, ContextStore

MSGS = [{"role": "system", "content": "kernel"}, {"role": "user", "content": "do it"}]


def test_disabled_by_default(tmp_path):
    assert enabled(None) is False
    assert inject({"cwd": str(tmp_path)}, MSGS) == MSGS


def test_enabled_by_config_or_env(monkeypatch):
    assert enabled({"context_runtime": True}) is True
    monkeypatch.setenv("DRYDOCK_CONTEXT_RUNTIME", "1")
    assert enabled({}) is True


def test_no_registered_store_is_a_noop(tmp_path):
    unregister(str(tmp_path))
    assert inject({"cwd": str(tmp_path), "context_runtime": True}, MSGS) == MSGS


def test_empty_store_injects_nothing(tmp_path):
    """Round one must be byte-identical to an uninstrumented run."""
    s = ContextStore(root=str(tmp_path), name="empty")
    register(str(tmp_path), s)
    try:
        assert inject({"cwd": str(tmp_path), "context_runtime": True}, MSGS) == MSGS
    finally:
        unregister(str(tmp_path))


def test_tombstones_reach_the_model_as_a_trailing_message(tmp_path):
    cr = ContextRatchet(str(tmp_path), run_id="inj1", goal="fix the parser")
    cr.on_round(round_no=1, action="rollback", passed=2, total=10,
                verifier_output="FAILED tests/test_p.py::test_nested\n",
                approach="rewrite the parser with a regex")
    register(str(tmp_path), cr.store)
    try:
        out = inject({"cwd": str(tmp_path), "context_runtime": True}, MSGS)
    finally:
        unregister(str(tmp_path))
    assert len(out) == len(MSGS) + 1
    assert out[:len(MSGS)] == MSGS                      # prefix untouched — the whole point
    assert out[-1]["role"] == "system"
    body = out[-1]["content"]
    assert "rewrite the parser with a regex" in body     # the ruled-out approach carries
    assert "tests/test_p.py::test_nested" in body        # with its evidence
    assert "Carried-forward context" in body


def test_packet_excludes_the_live_working_set(tmp_path):
    """WORKING is already in the transcript; re-sending it would duplicate context."""
    s = ContextStore(root=str(tmp_path), name="excl")
    s.put(ContextModule(context_id="ctx://w/now", body="CURRENT WORK"))
    s.put(ContextModule(context_id="ctx://s/repo", body="REPO FACTS", residency=SHARED))
    packet = build_packet(s)
    assert "REPO FACTS" in packet and "CURRENT WORK" not in packet


def test_packet_respects_its_budget(tmp_path):
    s = ContextStore(root=str(tmp_path), name="budget")
    s.put(ContextModule(context_id="ctx://s/big", body="X" * 30000, residency=SHARED))
    s.put(ContextModule(context_id="ctx://p/small", body="Y" * 60, residency=PINNED))
    packet = build_packet(s, budget=100)
    assert "Y" * 60 in packet                  # pinned stays
    assert "X" * 30000 not in packet           # oversized shared module evicted


def test_inject_never_raises_on_a_broken_store(tmp_path):
    class Boom:
        def all(self):
            raise RuntimeError("boom")
    register(str(tmp_path), Boom())
    try:
        assert inject({"cwd": str(tmp_path), "context_runtime": True}, MSGS) == MSGS
    finally:
        unregister(str(tmp_path))


def test_default_budget_is_small_because_it_rides_in_the_tail():
    assert DEFAULT_PACKET_BUDGET <= 4000

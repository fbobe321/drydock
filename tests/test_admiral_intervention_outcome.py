"""Regression: AdmiralWorker must classify each intervention's outcome
(unstuck vs. failed) and persist it via `record_intervention_outcome`.

Pre-fix, `record_intervention_outcome` was defined in persistence.py
and unit-tested but had ZERO production call sites. Result:
`prompt_failed` stayed at 0 forever, so
`finding_qualifies_for_code_change` (which requires prompt_failed >= 1)
never returned True. The whole Phase 3b proposer pipeline was silently
dormant — 1010 admiral findings accumulated, 0 ever qualified.

Post-fix, the worker tracks `_pending_interventions[code] = msg_count`
at apply time. On _tick:
- If the same code re-fires within INTERVENTION_FAIL_WINDOW_TURNS
  messages → record `unstuck=False`
- If the window elapses without a re-fire → record `unstuck=True`
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any

import pytest

from drydock.admiral import history, interventions, persistence, worker
from drydock.admiral.detectors import Finding


@dataclass
class _StubAgentLoop:
    session_id: str = "sess-test"
    # Mutable list so tests can grow it to simulate turns passing.
    messages: list = field(default_factory=list)


@pytest.fixture
def patched_world(monkeypatch):
    """Stub out persistence + history so we can observe outcome calls
    without touching disk."""
    captured = {"outcomes": [], "findings": []}

    monkeypatch.setattr(
        persistence, "record_finding",
        lambda code, sid: captured["findings"].append((code, sid)) or {
            "sessions": [sid], "total_fires": 1, "last_seen": "now"
        },
    )
    monkeypatch.setattr(
        persistence, "record_intervention_outcome",
        lambda code, unstuck: captured["outcomes"].append((code, unstuck)),
    )
    monkeypatch.setattr(
        persistence, "finding_qualifies_for_code_change",
        lambda code, **kw: False,
    )

    async def _stub_resolve(self, finding):
        return finding.directive, "canned"

    monkeypatch.setattr(worker.AdmiralWorker, "_resolve_directive", _stub_resolve)
    monkeypatch.setattr(history, "append", lambda *a, **kw: None)
    monkeypatch.setattr(interventions, "apply", lambda agent, finding: None)
    return captured


def _drain_pending(w: worker.AdmiralWorker):
    """Trigger any awaited tasks in the running loop."""
    pass  # asyncio.run handles this for us


def test_intervention_marked_failed_on_refire(patched_world):
    """Re-fire within the fail window → record outcome unstuck=False."""
    al = _StubAgentLoop(messages=[1, 2, 3, 4, 5])  # 5 messages at apply
    w = worker.AdmiralWorker(al)

    # Apply intervention via the public path
    finding = Finding(code="loop:read_file", directive="stop")
    asyncio.run(w._handle_finding(finding))

    # Worker recorded the apply-time message count
    assert w._pending_interventions == {"loop:read_file": 5}

    # Simulate 3 more messages, then the same finding fires again via _tick.
    al.messages.extend([6, 7, 8])

    # Patch detectors to return our re-fire and stub create_task (the
    # outcome-record path runs inline in _tick, before create_task).
    import drydock.admiral.worker as wmod
    original_run_all = wmod.detectors.run_all
    original_create = wmod.asyncio.create_task
    wmod.detectors.run_all = lambda msgs: [Finding(code="loop:read_file", directive="stop")]
    wmod.asyncio.create_task = lambda coro, **kw: coro.close() or None
    try:
        # Bypass dedup so the re-fire is observed
        w._recent_findings.clear()
        w._tick()
    finally:
        wmod.detectors.run_all = original_run_all
        wmod.asyncio.create_task = original_create

    assert ("loop:read_file", False) in patched_world["outcomes"], (
        "Expected `unstuck=False` outcome on re-fire within window"
    )
    # Pending entry must be cleared so we don't double-record
    assert "loop:read_file" not in w._pending_interventions


def test_intervention_marked_unstuck_when_window_elapses(patched_world):
    """No re-fire within window → record outcome unstuck=True."""
    al = _StubAgentLoop(messages=list(range(5)))
    w = worker.AdmiralWorker(al)

    finding = Finding(code="empty_after_tool:bash", directive="continue")
    asyncio.run(w._handle_finding(finding))
    assert "empty_after_tool:bash" in w._pending_interventions

    # Push past the fail window (10 turns) without any re-fires
    al.messages.extend(list(range(20)))  # 25 total, delta 20 > 10

    import drydock.admiral.worker as wmod
    original = wmod.detectors.run_all
    wmod.detectors.run_all = lambda msgs: []  # no findings this tick
    try:
        w._tick()
    finally:
        wmod.detectors.run_all = original

    assert ("empty_after_tool:bash", True) in patched_world["outcomes"], (
        "Expected `unstuck=True` outcome after window elapsed without re-fire"
    )
    assert "empty_after_tool:bash" not in w._pending_interventions


def test_intervention_outcome_persistence_swallows_errors(patched_world, monkeypatch):
    """Persistence failure must NOT crash Admiral (rule: Admiral never
    takes drydock down)."""
    def boom(code, unstuck):
        raise RuntimeError("disk full")
    monkeypatch.setattr(persistence, "record_intervention_outcome", boom)

    w = worker.AdmiralWorker(_StubAgentLoop())
    # Should not raise
    w._record_outcome("loop:foo", unstuck=True)


def test_record_intervention_outcome_has_production_callsite():
    """Belt-and-suspenders: catch a future revert that would re-orphan
    this function. Only worker.py + tests should reference it."""
    src = inspect.getsource(worker)
    assert "record_intervention_outcome" in src, (
        "AdmiralWorker no longer wires intervention outcomes to "
        "persistence — Phase 3b promotion criteria will be dormant. "
        "See worker._record_outcome / _resolve_elapsed_interventions."
    )

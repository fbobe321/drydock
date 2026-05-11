"""Regression: Admiral must record findings against the live
`AgentLoop.session_id` (which matches the on-disk session log dir),
NOT a fresh phantom uuid.

Pre-fix, agent_loop.py:284 set `self._admiral_session_id = uuid4()`
and worker.py:105 read that attribute. The result was 1010 admiral
findings keyed to UUIDs that never resolved to any session dir under
~/.drydock/logs/session/, breaking M5/Deep Noir's pair extraction.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any

import pytest

from drydock.admiral import history, interventions, llm_analyzer, persistence, worker
from drydock.admiral.detectors import Finding


@dataclass
class _StubAgentLoop:
    """Minimal stand-in for AgentLoop — only the attrs the worker reads."""
    session_id: str = "real-session-abc-123"
    messages: tuple = ()


def test_worker_records_finding_with_live_session_id(monkeypatch):
    """When _handle_finding fires, persistence.record_finding must
    receive the agent_loop's `session_id` — not a fresh uuid, not "".
    """
    captured: list[tuple[str, str]] = []

    monkeypatch.setattr(
        persistence, "record_finding",
        lambda code, sid: captured.append((code, sid)) or {
            "sessions": [sid], "total_fires": 1, "last_seen": "now"
        },
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

    al = _StubAgentLoop(session_id="known-real-session-xyz")
    w = worker.AdmiralWorker(al)
    finding = Finding(code="loop:read_file", directive="stop reading")

    asyncio.run(w._handle_finding(finding))

    assert captured == [("loop:read_file", "known-real-session-xyz")], (
        "Admiral recorded finding against wrong session id — pair extraction "
        "will fail. See agent_loop.py:280 + worker.py:105."
    )


def test_worker_source_does_not_reference_phantom_uuid_attr():
    """Belt-and-suspenders: the source must read `session_id`, not the
    pre-fix `_admiral_session_id` attribute. Catches accidental reverts."""
    src = inspect.getsource(worker.AdmiralWorker._handle_finding)
    assert "_admiral_session_id" not in src or src.find("_admiral_session_id") > src.find("Pre-fix"), (
        "_handle_finding still reads `_admiral_session_id` outside of a "
        "comment. Use `agent_loop.session_id` so M5 can resolve sessions "
        "to on-disk dirs."
    )
    assert "agent_loop, \"session_id\"" in src or "agent_loop, 'session_id'" in src, (
        "_handle_finding must read `session_id` from the live agent_loop."
    )


def test_agent_loop_does_not_set_phantom_uuid_attr():
    """The AgentLoop init must NOT create a separate `_admiral_session_id`
    attribute that diverges from `session_id`."""
    from drydock.core import agent_loop as agent_loop_mod
    src = inspect.getsource(agent_loop_mod)
    # The attribute may appear in a comment explaining the fix; it must
    # not appear as an assignment outside a comment.
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "self._admiral_session_id" not in stripped, (
            "agent_loop.py still assigns `self._admiral_session_id` — "
            "this UUID never resolves to an on-disk session dir. Use "
            "`self.session_id` directly."
        )

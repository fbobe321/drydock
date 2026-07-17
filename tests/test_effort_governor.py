"""Tests for the time-aware effort governor: a turn overrunning the soft cap
flips the rest of the request to decisive (low effort, tight token cap, forcing
suffix). Sticky per request; cap=0 disables."""
from __future__ import annotations

import tempfile
import time

import drydock.agent as agent_mod
from drydock.agent import _DECISIVE_MAX_TOKENS, AgentState, run
from drydock.providers import AssistantTurn


def _slow_then_fast(captured):
    """Turn 1 sleeps past the cap; later turns record the config they were
    called with."""
    def stream(**kw):
        n = stream.n
        stream.n += 1
        captured.append({
            "effort": kw.get("config", {}).get("reasoning_effort"),
            "max_tokens": kw.get("config", {}).get("max_tokens"),
            "decisive_suffix": "DECISIVE" in (kw.get("system") or "").upper()
                                or "single" in (kw.get("system") or "").lower(),
        })
        if n == 0:
            time.sleep(0.05)  # overruns the tiny test cap
            return iter([AssistantTurn(
                "", [{"id": "1", "name": "Bash", "input": {"command": "echo hi"}}], 1, 1)])
        if n == 1:
            return iter([AssistantTurn(
                "", [{"id": "2", "name": "Bash", "input": {"command": "echo again"}}], 1, 1)])
        return iter([AssistantTurn("done", [], 1, 1)])
    stream.n = 0
    return stream


def test_overrun_turn_flips_request_to_decisive(monkeypatch):
    captured: list = []
    monkeypatch.setattr(agent_mod, "stream", _slow_then_fast(captured))
    st = AgentState()
    list(run("do things", st,
             {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False,
              "turn_seconds_soft_cap": 0.01}, "sys"))
    assert len(captured) >= 3
    # turn 1 ran normally; turns 2+ are decisive
    assert captured[1]["effort"] == "low"
    assert captured[1]["max_tokens"] == _DECISIVE_MAX_TOKENS
    assert captured[2]["effort"] == "low"
    # the run still completes normally
    assert any(m.get("content") == "done" for m in st.messages)


def test_cap_zero_disables_governor(monkeypatch):
    captured: list = []
    monkeypatch.setattr(agent_mod, "stream", _slow_then_fast(captured))
    st = AgentState()
    list(run("do things", st,
             {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False,
              "turn_seconds_soft_cap": 0}, "sys"))
    # no decisive flip: later turns keep whatever effort the normal logic picked
    assert captured[1]["max_tokens"] != _DECISIVE_MAX_TOKENS


def test_fast_turns_never_trigger(monkeypatch):
    captured: list = []

    def fast(**kw):
        n = fast.n; fast.n += 1
        captured.append({"max_tokens": kw.get("config", {}).get("max_tokens")})
        if n == 0:
            return iter([AssistantTurn(
                "", [{"id": "1", "name": "Bash", "input": {"command": "echo hi"}}], 1, 1)])
        return iter([AssistantTurn("done", [], 1, 1)])
    fast.n = 0
    monkeypatch.setattr(agent_mod, "stream", fast)
    st = AgentState()
    list(run("quick", st,
             {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False,
              "turn_seconds_soft_cap": 240}, "sys"))
    assert captured[1]["max_tokens"] != _DECISIVE_MAX_TOKENS
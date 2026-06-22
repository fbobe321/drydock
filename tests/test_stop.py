"""STOP/interrupt: a threading.Event in config["_cancel"] lets the TUI halt a
running turn at a safe point. The agent must (a) not start a turn when stop is
already set, and (b) end after the current turn's tools rather than looping —
without leaving an assistant tool_call missing its tool result."""
from __future__ import annotations

import threading

import drydock.agent as agent_mod
from drydock.agent import AgentState, run
from drydock.providers import AssistantTurn


def test_no_turn_when_already_stopped(monkeypatch):
    calls = {"n": 0}

    def fake_stream(**kw):
        calls["n"] += 1
        yield AssistantTurn("hi", [], 1, 1)

    monkeypatch.setattr(agent_mod, "stream", fake_stream)
    ev = threading.Event()
    ev.set()
    st = AgentState()
    list(run("go", st, {"model": "m", "_cancel": ev}, "sys"))
    assert calls["n"] == 0  # loop broke before any model call


def test_stop_ends_after_current_turn(monkeypatch):
    # The model would loop forever (always returns a tool call); a stop set
    # during the first turn must end the run after that turn's tools.
    calls = {"n": 0}
    ev = threading.Event()

    def fake_stream(**kw):
        calls["n"] += 1
        ev.set()  # user hits STOP while this turn runs
        yield AssistantTurn(
            "", [{"id": str(calls["n"]), "name": "Read", "input": {"file_path": "nope"}}], 1, 1
        )

    monkeypatch.setattr(agent_mod, "stream", fake_stream)
    st = AgentState()
    list(run("go", st, {"model": "m", "_cancel": ev}, "sys"))
    assert calls["n"] == 1  # exactly one turn, then stopped

    # History is still valid: every assistant tool_call has a matching tool msg.
    call_ids = [
        tc["id"]
        for msg in st.messages
        if msg.get("role") == "assistant"
        for tc in (msg.get("tool_calls") or [])
    ]
    tool_ids = [m["tool_call_id"] for m in st.messages if m["role"] == "tool"]
    assert sorted(call_ids) == sorted(tool_ids)


def test_no_cancel_event_runs_normally(monkeypatch):
    # Absent a _cancel event, behaviour is unchanged.
    seq = [AssistantTurn("done", [], 1, 1)]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    list(run("go", st, {"model": "m"}, "sys"))
    assert any(m.get("role") == "assistant" for m in st.messages)


def test_create_abortable_raises_on_cancel():
    """_create_abortable runs the blocking call off-thread and abandons it when
    the cancel Event fires — so STOP doesn't wait out a long decode."""
    import threading
    import time
    import pytest
    from drydock.providers import _create_abortable, _StopRequested

    cancel = threading.Event()

    class _SlowClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    time.sleep(30)  # simulate a long decode (orphaned on cancel)
                    return "never"

    threading.Timer(0.3, cancel.set).start()
    t0 = time.monotonic()
    with pytest.raises(_StopRequested):
        _create_abortable(_SlowClient(), {}, "", "", cancel)
    assert time.monotonic() - t0 < 5  # returned promptly, didn't wait 30s

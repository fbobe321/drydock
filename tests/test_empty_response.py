"""A completely empty assistant response (no text, no tool call, no leaked
call) should be nudged ONCE to produce output rather than dead-ending the user
with a silent stop — but it must not loop on real (non-empty) answers."""
from __future__ import annotations

import drydock.agent as agent_mod
from drydock.agent import AgentState, run
from drydock.providers import AssistantTurn


def test_empty_response_gets_one_nudge(monkeypatch):
    seq = [
        AssistantTurn("", [], 1, 1),          # empty → nudge
        AssistantTurn("here is the answer", [], 1, 1),  # then a real reply
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    list(run("do it", st, {"model": "m"}, "sys"))
    nudges = [m for m in st.messages
              if m["role"] == "user" and "response was empty" in m["content"]]
    assert len(nudges) == 1
    assert not seq  # both turns consumed


def test_empty_nudge_capped_at_one(monkeypatch):
    # Persistently empty → nudged once, then the loop gives control back.
    seq = [AssistantTurn("", [], 1, 1) for _ in range(6)]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    list(run("do it", st, {"model": "m"}, "sys"))
    nudges = [m for m in st.messages
              if m["role"] == "user" and "response was empty" in m["content"]]
    assert len(nudges) == 1  # not a spin


def test_nonempty_text_is_not_nudged(monkeypatch):
    seq = [AssistantTurn("all done", [], 1, 1)]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    list(run("hi", st, {"model": "m"}, "sys"))
    assert not [m for m in st.messages
                if m["role"] == "user" and "response was empty" in m["content"]]

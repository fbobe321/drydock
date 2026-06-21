"""The agent must not stall mid-plan: when the model lays out a `todo` and then
stops with text only, it should be nudged to keep going (capped + env-gated),
not return control to the user as if the work were done."""
from __future__ import annotations

import drydock.agent as agent_mod
from drydock.agent import AgentState, run
from drydock.providers import AssistantTurn


def _drive(turns, config):
    """Run the loop over a fixed list of model turns; the todo state is set in
    config to simulate a plan that already has unfinished steps."""
    seq = list(turns)
    monkey = lambda **kw: iter([seq.pop(0)])
    return seq, monkey


def test_unfinished_plan_gets_continue_nudge(monkeypatch):
    # Model stops with text only while a plan has unfinished steps → it must be
    # nudged once; then it does the work (marks the todo done, which runs the
    # real tool and clears the unfinished state) and wraps up without re-nudging.
    seq = [
        AssistantTurn("Here is my plan. Shall I proceed?", [], 1, 1),
        AssistantTurn("", [{"id": "1", "name": "todo",
                            "input": {"tasks": "[x] step one\n[x] step two"}}], 1, 1),
        AssistantTurn("all done", [], 1, 1),
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    cfg = {"model": "m", "_todo": [("step one", "in_progress"), ("step two", "pending")]}
    list(run("build it", st, cfg, "sys"))
    nudges = [m for m in st.messages
              if m["role"] == "user" and "unfinished steps" in m["content"]]
    assert len(nudges) == 1


def test_finished_plan_does_not_nudge(monkeypatch):
    # Every step done → no nudge, the turn ends normally.
    seq = [AssistantTurn("finished everything", [], 1, 1)]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    cfg = {"model": "m", "_todo": [("step one", "done"), ("step two", "done")]}
    list(run("build it", st, cfg, "sys"))
    assert not [m for m in st.messages
                if m["role"] == "user" and "unfinished steps" in m["content"]]


def test_no_plan_does_not_nudge(monkeypatch):
    # Plain chat with no todo → never nudged.
    seq = [AssistantTurn("hi there", [], 1, 1)]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    list(run("hello", st, {"model": "m"}, "sys"))
    assert not [m for m in st.messages
                if m["role"] == "user" and "unfinished steps" in m["content"]]


def test_consecutive_stalls_are_capped(monkeypatch):
    # If the model keeps stalling without progress, nudges stop at the cap so
    # the loop can never wedge.
    seq = [AssistantTurn("still asking to proceed", [], 1, 1) for _ in range(10)]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    cfg = {"model": "m", "_todo": [("step", "pending")]}
    list(run("go", st, cfg, "sys"))
    nudges = [m for m in st.messages
              if m["role"] == "user" and "unfinished steps" in m["content"]]
    assert len(nudges) == agent_mod.PLAN_CONTINUE_CAP


def test_env_disable_turns_off_autocontinue(monkeypatch):
    monkeypatch.setenv("DRYDOCK_PLAN_AUTOCONTINUE_DISABLE", "1")
    seq = [AssistantTurn("shall I proceed?", [], 1, 1)]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    cfg = {"model": "m", "_todo": [("step", "pending")]}
    list(run("go", st, cfg, "sys"))
    assert not [m for m in st.messages
                if m["role"] == "user" and "unfinished steps" in m["content"]]

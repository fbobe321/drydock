"""Tests for the tool_started/tool_completed split + in-flight detection (PRD
P2.2), the foundation for task resume."""
from __future__ import annotations

import tempfile

import drydock.agent as agent_mod
from drydock.agent import AgentState, run
from drydock.events import EventLog, find_unresolved
from drydock.providers import AssistantTurn


def _log():
    return EventLog(tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name)


def test_no_unresolved_when_all_tools_complete():
    ev = _log()
    ev.emit("tool_started", name="Read", effect="read_only")
    ev.emit("tool", name="Read", status="ok")
    ev.emit("tool_started", name="Bash", effect="local_mutation")
    ev.emit("tool", name="Bash", status="ok")
    assert find_unresolved(ev.path) == []


def test_interrupted_tool_is_unresolved():
    # started but never completed -> the process died mid-tool.
    ev = _log()
    ev.emit("tool_started", name="Read", effect="read_only")
    ev.emit("tool", name="Read", status="ok")
    ev.emit("tool_started", name="Bash", effect="local_mutation")  # crash here
    unresolved = find_unresolved(ev.path)
    assert len(unresolved) == 1
    assert unresolved[0]["name"] == "Bash"
    assert unresolved[0]["effect"] == "local_mutation"


def test_external_mutation_in_flight_is_flagged():
    # P2.3: an unresolved external mutation must be identifiable so it's not
    # blindly retried.
    ev = _log()
    ev.emit("tool_started", name="mcp__github__create_issue", effect="external_mutation")
    unresolved = find_unresolved(ev.path)
    assert unresolved[0]["effect"] == "external_mutation"


def test_agent_loop_emits_started_before_completed(monkeypatch):
    # End-to-end: the real loop emits tool_started then a tool (completed) event.
    ev = _log()
    turns = [
        AssistantTurn("", [{"id": "1", "name": "Bash",
                            "input": {"command": "echo hi"}}], 1, 1),
        AssistantTurn("done", [], 1, 1),
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([turns.pop(0)]))
    st = AgentState()
    st.events = ev
    list(run("go", st, {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False}, "sys"))
    types = [e["type"] for e in EventLog.read(ev.path)]
    assert "tool_started" in types
    i = types.index("tool_started")
    assert "tool" in types[i:]           # a completion follows the start
    assert find_unresolved(ev.path) == []  # nothing left dangling on a clean run

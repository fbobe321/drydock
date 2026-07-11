"""Durable event log (Agent-Buildout PRD, Epic: events) — the run appends a JSONL
execution trace that can be read back for inspection/replay."""
from __future__ import annotations

import os
import tempfile

from drydock import agent
from drydock.events import EventLog
from drydock.providers import AssistantTurn


def test_emit_and_read_roundtrip():
    p = os.path.join(tempfile.mkdtemp(), "e.jsonl")
    log = EventLog(p)
    log.emit("task_start", objective="do X", acceptance_criteria=["a", "b"])
    log.emit("tool", name="Write", result_chars=10)
    evs = EventLog.read(p)
    assert [e["type"] for e in evs] == ["task_start", "tool"]
    assert evs[0]["seq"] == 1 and evs[1]["seq"] == 2 and "ts" in evs[0]
    assert evs[0]["acceptance_criteria"] == ["a", "b"]


def test_read_missing_file_is_empty():
    assert EventLog.read("/no/such/file.jsonl") == []


def test_emit_never_raises_on_bad_path():
    EventLog("/root/cannot/write/here.jsonl").emit("x", a=1)  # no exception


def test_run_writes_full_trace():
    p = os.path.join(tempfile.mkdtemp(), "run.jsonl")
    st = agent.AgentState(); st.events = EventLog(p)
    turns = [
        AssistantTurn("", [{"id": "1", "name": "Write",
                            "input": {"file_path": "/tmp/dd_e.txt", "content": "x"}}], 10, 5),
        AssistantTurn("", [{"id": "2", "name": "Bash", "input": {"command": "python -c '1'"}}], 8, 4),
        AssistantTurn("done, verified", [], 6, 3),
    ]
    i = {"n": 0}
    def fake(model, system, messages, tool_schemas, config):
        t = turns[min(i["n"], len(turns) - 1)]; i["n"] += 1; yield t
    orig = agent.stream; agent.stream = fake
    try:
        list(agent.run("Do the task.\n1. write out.txt", st,
                       {"model": "gemma4", "max_tokens": 8192, "context_limit": 65536}, "SYS"))
    finally:
        agent.stream = orig
    evs = EventLog.read(p)
    types = [e["type"] for e in evs]
    assert types[0] == "task_start" and "turn" in types and "tool" in types and types[-1] == "done"
    assert next(e for e in evs if e["type"] == "done")["phase"] == "complete"


def test_disabled_no_events_attr():
    st = agent.AgentState()
    assert st.events is None  # off by default on a bare AgentState

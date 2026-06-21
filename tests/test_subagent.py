"""Tests for the read-only `task` sub-agent tool.

The sub-agent runs a fresh agent loop with a restricted toolset. These tests
drive it through a fake `stream` (no real model) to verify the contract:
  * it returns the sub-agent's final text as the tool result;
  * it is offered ONLY the read-only tools (no Write/Edit/todo/task);
  * a call to a disallowed tool is REFUSED, never executed.
"""
from __future__ import annotations

import drydock.agent as agent_mod
from drydock.providers import AssistantTurn
from drydock.tools import tool_task, SUBAGENT_TOOLS


def test_task_returns_subagent_summary(monkeypatch):
    # Sub-agent does one Grep, then summarizes.
    seq = [
        AssistantTurn("", [{"id": "1", "name": "Grep", "input": {"pattern": "x"}}], 1, 1),
        AssistantTurn("auth lives in auth.py:42", [], 1, 1),
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    out = tool_task({"prompt": "where is auth handled?"}, {"model": "m"})
    assert out == "auth lives in auth.py:42"


def test_task_offers_only_readonly_tools(monkeypatch):
    seen = {}

    def fake_stream(model, system, messages, tool_schemas, config):
        seen["names"] = {s["name"] for s in tool_schemas}
        yield AssistantTurn("done", [], 1, 1)

    monkeypatch.setattr(agent_mod, "stream", fake_stream)
    tool_task({"prompt": "look around"}, {"model": "m"})
    assert seen["names"] == set(SUBAGENT_TOOLS)
    assert "Write" not in seen["names"] and "Edit" not in seen["names"]
    assert "task" not in seen["names"]  # no recursion


def test_task_refuses_disallowed_tool(monkeypatch, tmp_path):
    # Even if the sub-agent model hallucinates an Edit, it must be REFUSED, not
    # executed — the file stays untouched.
    fp = tmp_path / "f.txt"
    fp.write_text("original\n")
    seq = [
        AssistantTurn("", [{"id": "1", "name": "Edit", "input": {
            "file_path": str(fp), "old_string": "original", "new_string": "HACKED"}}], 1, 1),
        AssistantTurn("could not edit; here is what I found", [], 1, 1),
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    out = tool_task({"prompt": "try to edit"}, {"model": "m", "cwd": str(tmp_path)})
    assert fp.read_text() == "original\n"  # untouched
    assert "could not edit" in out


def test_task_requires_prompt():
    assert "needs a `prompt`" in tool_task({}, {"model": "m"})

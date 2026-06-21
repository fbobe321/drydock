"""Tests for graceful handling of hallucinated tool names."""
from __future__ import annotations

import drydock.agent as agent_mod
from drydock.agent import AgentState, run
from drydock.providers import AssistantTurn
from drydock.tuning import hallucinated_tool_message


def test_known_hallucinated_names_get_redirect():
    for name in ["exit_plan_mode", "lsp", "read_mcp_resource", "ralph_repo_index"]:
        assert hallucinated_tool_message(name) is not None


def test_real_tools_are_not_redirected():
    # `todo` and `task` are real tools now — they must NOT be redirected.
    for name in ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "todo", "task"]:
        assert hallucinated_tool_message(name) is None


def test_agent_redirects_hallucinated_call_without_executing(monkeypatch):
    # Model calls a hallucinated tool, then (seeing the redirect) finishes.
    seq = [
        AssistantTurn("", [{"id": "1", "name": "exit_plan_mode", "input": {}}], 1, 1),
        AssistantTurn("done", [], 1, 1),
    ]

    def fake_stream(model, system, messages, tool_schemas, config):
        yield seq.pop(0)

    monkeypatch.setattr(agent_mod, "stream", fake_stream)
    st = AgentState()
    list(run("go", st, {"model": "gemma4"}, "sys"))

    # The tool result stored for the hallucinated call is the benign redirect,
    # not a "tool not found" error.
    tool_msgs = [m for m in st.messages if m["role"] == "tool"]
    assert tool_msgs and "plan mode" in tool_msgs[0]["content"].lower()
    assert "not found" not in tool_msgs[0]["content"].lower()

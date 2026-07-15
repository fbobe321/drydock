"""Tests for tool effect classification + approval policy (PRD Epic H)."""
from __future__ import annotations

from drydock.tool_policy import (
    ToolEffect,
    effect_of,
    policy_for,
    redact,
    requires_approval,
)


def test_read_only_tool_is_automatic():
    # PRD H1.1
    assert effect_of("Read", read_only=True) == ToolEffect.READ_ONLY
    assert not requires_approval("Read", read_only=True)


def test_local_mutation_is_automatic():
    assert effect_of("Write") == ToolEffect.LOCAL_MUTATION
    assert not requires_approval("Write")


def test_mcp_tool_is_external_mutation_and_needs_approval():
    # PRD H1.2: an MCP tool that creates a GitHub issue requires approval.
    assert effect_of("mcp__github__create_issue") == ToolEffect.EXTERNAL_MUTATION
    assert requires_approval("mcp__github__create_issue")


def test_declared_effect_wins():
    assert effect_of("Bash", declared=ToolEffect.DESTRUCTIVE) == ToolEffect.DESTRUCTIVE
    assert requires_approval("Bash", declared=ToolEffect.DESTRUCTIVE)


def test_config_override_can_make_external_automatic():
    cfg = {"tool_policy": {"external_mutation": "automatic"}}
    assert policy_for(ToolEffect.EXTERNAL_MUTATION, cfg) == "automatic"
    assert not requires_approval("mcp__github__create_issue", config=cfg)


def test_web_tools_are_read_only():
    assert effect_of("WebFetch") == ToolEffect.READ_ONLY
    assert not requires_approval("WebSearch")


def test_credential_access_requires_approval():
    assert policy_for(ToolEffect.CREDENTIAL_ACCESS) == "approval"


def test_redact_masks_configured_fields():
    # PRD H1.4: sensitive fields are masked before the model sees them.
    out = redact({"user": "bob", "password": "hunter2", "rows": [{"token": "x"}]},
                 ["password", "token"])
    assert out["password"] == "***"
    assert out["user"] == "bob"
    assert out["rows"][0]["token"] == "***"


def test_redact_noop_without_fields():
    data = {"a": 1}
    assert redact(data, []) == data


def test_external_tool_declined_in_loop_is_refused(monkeypatch):
    # PRD H1.2/H1.3: an external-mutation tool pauses for approval; a decline
    # returns DENIED/REFUSED and the tool does not run, task stays alive.
    import tempfile

    import drydock.agent as agent_mod
    from drydock.agent import AgentState, run
    from drydock.providers import AssistantTurn

    turns = [
        AssistantTurn("", [{"id": "1", "name": "mcp__github__create_issue",
                            "input": {"title": "bug"}}], 1, 1),
        AssistantTurn("understood, not creating it", [], 1, 1),
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([turns.pop(0)]))
    st = AgentState()
    seen = {}

    def approver(desc, reason):
        seen["reason"] = reason
        return "deny"
    list(run("file an issue", st,
             {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False,
              "request_approval": approver}, "sys"))
    tool_msgs = [m for m in st.messages if m.get("role") == "tool"]
    assert tool_msgs and "REFUSED" in tool_msgs[0]["content"]
    assert "external_mutation" in seen.get("reason", "")


def test_external_tool_approved_always_sets_flag(monkeypatch):
    import tempfile

    import drydock.agent as agent_mod
    from drydock.agent import AgentState, run
    from drydock.providers import AssistantTurn

    turns = [
        AssistantTurn("", [{"id": "1", "name": "mcp__x__do",
                            "input": {}}], 1, 1),
        AssistantTurn("done", [], 1, 1),
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([turns.pop(0)]))
    st = AgentState()
    cfg = {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False,
           "request_approval": lambda d, r: "always"}
    list(run("do it", st, cfg, "sys"))
    assert cfg.get("_approve_all") is True  # "always" persists for the session

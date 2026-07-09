"""Content-based repetition trigger for the over-think interrupt: a non-streaming
turn that collapses into a pure loop AND produces no action escalates straight to
decisive mode. It must NOT fire on varied reasoning, on a turn with a tool call,
or when the interrupt is disabled (stall_secs=0)."""
from __future__ import annotations

import pytest

from drydock import agent, providers
from drydock.providers import AssistantTurn, RepetitionDetected, _complete_nonstreaming


class _Fn:
    def __init__(self, name, args): self.name = name; self.arguments = args
class _TC:
    def __init__(self, name, args): self.id = "c1"; self.function = _Fn(name, args)
class _Msg:
    def __init__(self, content, tcs=None): self.content = content; self.tool_calls = tcs or []
class _Choice:
    def __init__(self, msg): self.message = msg
class _Resp:
    def __init__(self, content, tcs=None): self.choices = [_Choice(_Msg(content, tcs))]; self.usage = None


def _patch(monkeypatch, content, tcs=None):
    monkeypatch.setattr(providers, "_create_abortable", lambda *a, **k: _Resp(content, tcs))


def test_pure_loop_no_action_raises(monkeypatch):
    _patch(monkeypatch, "loop " * 300)   # 1500 chars of one unit
    with pytest.raises(RepetitionDetected):
        list(_complete_nonstreaming(None, {}, stall_secs=300))


def test_varied_reasoning_does_not_raise(monkeypatch):
    _patch(monkeypatch, "First check the file, then run grep, then write the result and verify it.")
    out = list(_complete_nonstreaming(None, {}, stall_secs=300))
    assert any(isinstance(o, AssistantTurn) for o in out)  # completed normally


def test_loop_but_has_toolcall_does_not_raise(monkeypatch):
    _patch(monkeypatch, "loop " * 300, [_TC("Bash", '{"command":"ls"}')])
    out = list(_complete_nonstreaming(None, {}, stall_secs=300))
    turn = [o for o in out if isinstance(o, AssistantTurn)][0]
    assert turn.tool_calls and turn.tool_calls[0]["name"] == "Bash"  # action preserved


def test_disabled_when_stall_secs_zero(monkeypatch):
    _patch(monkeypatch, "loop " * 300)
    out = list(_complete_nonstreaming(None, {}, stall_secs=0))  # feature off
    assert any(isinstance(o, AssistantTurn) for o in out)


def test_agent_goes_decisive_immediately_on_repetition(monkeypatch):
    calls = []
    def fake_stream(model, system, messages, tool_schemas, config):
        calls.append({"system": system, "max_tokens": config.get("max_tokens")})
        if len(calls) == 1:
            raise RepetitionDetected(0)
        yield AssistantTurn("done", [], 10, 5)
    monkeypatch.setattr(agent, "stream", fake_stream)
    st = agent.AgentState()
    cfg = {"model": "gemma4", "max_tokens": 8192, "stall_retry_secs": 300, "context_limit": 65536}
    list(agent.run("go", st, cfg, "SYS"))
    # first call raised repetition → SECOND call is already decisive (no plain retry)
    assert len(calls) == 2
    assert agent._DECISIVE_SUFFIX in calls[1]["system"]
    assert calls[1]["max_tokens"] <= agent._DECISIVE_MAX_TOKENS

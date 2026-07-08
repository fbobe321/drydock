"""Over-think interrupt: when a call keeps stalling/over-thinking (repeated
StallRetry), the agent escalates to 'decisive mode' — a forcing suffix on the
system prompt + a hard max_tokens cap — so the model can't burn a 5k-token
reasoning turn without acting. First retry re-issues as-is; 2nd+ goes decisive."""
from __future__ import annotations

from drydock import agent
from drydock.agent import AgentState, _DECISIVE_SUFFIX, _DECISIVE_MAX_TOKENS
from drydock.providers import StallRetry, AssistantTurn


def _drive(monkeypatch, stalls):
    calls = []

    def fake_stream(model, system, messages, tool_schemas, config):
        calls.append({"system": system, "max_tokens": config.get("max_tokens"),
                      "effort": config.get("reasoning_effort")})
        if len(calls) <= stalls:
            raise StallRetry(1)
        yield AssistantTurn(text="done", tool_calls=[], input_tokens=10, output_tokens=5)

    monkeypatch.setattr(agent, "stream", fake_stream)
    state = AgentState()
    cfg = {"model": "gemma4", "max_tokens": 8192, "stall_retry_secs": 1, "context_limit": 65536}
    list(agent.run("do it", state, cfg, "SYS"))
    return calls


def test_first_retry_not_decisive_second_is(monkeypatch):
    calls = _drive(monkeypatch, stalls=2)   # stall twice, succeed on 3rd
    assert len(calls) == 3
    assert _DECISIVE_SUFFIX not in calls[0]["system"]      # attempt 1: as-is
    assert _DECISIVE_SUFFIX not in calls[1]["system"]      # attempt 2 (retry 1): as-is
    assert _DECISIVE_SUFFIX in calls[2]["system"]          # attempt 3 (retry 2): decisive
    assert calls[2]["max_tokens"] <= _DECISIVE_MAX_TOKENS   # hard token cap
    assert calls[2]["effort"] == "low"


def test_gives_up_after_bounded_retries(monkeypatch):
    # stalls forever → bounded (>3) → ends cleanly, doesn't loop forever
    calls = _drive(monkeypatch, stalls=99)
    assert len(calls) <= 5   # 1 initial + up to ~3 retries then give up


def test_no_stall_no_decisive(monkeypatch):
    calls = _drive(monkeypatch, stalls=0)   # succeeds first try
    assert len(calls) == 1 and _DECISIVE_SUFFIX not in calls[0]["system"]

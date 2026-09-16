"""A turn that hits max_tokens without a tool call (runaway planning, or a whole-file
write that didn't fit) is not "done": the agent nudges it to act in smaller steps, trims
the runaway text from history, and stays bounded."""
from __future__ import annotations

from drydock import agent
from drydock.agent import AgentState
from drydock.providers import AssistantTurn


def _drive(monkeypatch, truncations):
    calls = []

    def fake_stream(model, system, messages, tool_schemas, config):
        calls.append([m.get("content") for m in messages])
        if len(calls) <= truncations:
            yield AssistantTurn(text="plan " * 5000, tool_calls=[], input_tokens=10,
                                output_tokens=8192, truncated=True)
        else:
            yield AssistantTurn(text="done", tool_calls=[], input_tokens=10, output_tokens=5)

    monkeypatch.setattr(agent, "stream", fake_stream)
    state = AgentState()
    cfg = {"model": "m", "max_tokens": 8192, "context_limit": 65536}
    events = list(agent.run("do it", state, cfg, "SYS"))
    return calls, state, events


def test_truncated_turn_nudges_and_trims(monkeypatch):
    calls, state, events = _drive(monkeypatch, truncations=1)
    assert len(calls) == 2
    assert any("output-length limit" in (c or "") for c in calls[1])
    planning = [m for m in state.messages if m["role"] == "assistant"][0]["content"]
    assert len(planning) < 2000 and "cut off" in planning
    assert any("output limit reached" in getattr(e, "text", "") for e in events)


def test_truncation_nudges_are_bounded(monkeypatch):
    calls, _, _ = _drive(monkeypatch, truncations=99)
    assert len(calls) == 4          # 1 + 3 nudges, then accepted as the end


def test_normal_text_turn_is_not_nudged(monkeypatch):
    calls, _, _ = _drive(monkeypatch, truncations=0)
    assert len(calls) == 1

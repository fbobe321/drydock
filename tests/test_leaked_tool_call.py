"""Tests for recovery from text-form (leaked) tool calls.

Gemma sometimes emits `<|tool_call>call:write_file{...}<tool_call|>` as text.
The API can't structure it, so nothing runs. The harness must hide the blob
and nudge a clean retry rather than ending the turn with no work done.
"""
from __future__ import annotations

import drydock.agent as agent_mod
from drydock.agent import AgentState, run
from drydock.providers import AssistantTurn, TextChunk
from drydock.tuning import strip_leaked_tool_calls


# ── the strip/detect helper ────────────────────────────────────────────────

def test_strip_removes_leaked_blob_and_reports_found():
    raw = "Sure!\n<|tool_call>call:write_file{path: 'x.py'}<tool_call|>\nDone."
    cleaned, found = strip_leaked_tool_calls(raw)
    assert found
    assert "<|tool_call>" not in cleaned and "write_file" not in cleaned
    assert "Sure!" in cleaned and "Done." in cleaned


def test_strip_ignores_ordinary_prose():
    text = "I will make a tool call to write the file."
    cleaned, found = strip_leaked_tool_calls(text)
    assert not found and cleaned == text


def test_strip_handles_bare_markers():
    cleaned, found = strip_leaked_tool_calls("oops <|tool_call> half")
    assert found and "<|tool_call>" not in cleaned


# ── agent loop nudges a retry on a leaked call ─────────────────────────────

def test_agent_retries_after_leaked_call(monkeypatch):
    calls = {"n": 0}

    def fake_stream(model, system, messages, tool_schemas, config):
        calls["n"] += 1
        if calls["n"] == 1:
            # First turn: the model "leaked" a tool call as text.
            yield AssistantTurn("(blob hidden)", [], 5, 5, had_leaked_call=True)
        else:
            # After the nudge it answers cleanly (no tool calls → turn ends).
            yield TextChunk("Done for real.")
            yield AssistantTurn("Done for real.", [], 5, 5)

    monkeypatch.setattr(agent_mod, "stream", fake_stream)
    st = AgentState()
    list(run("build it", st, {"model": "gemma4"}, "sys"))

    assert calls["n"] == 2  # it retried instead of stopping after the leak
    # The corrective nudge was injected into the conversation.
    assert any(
        m["role"] == "user" and "did not run" in m.get("content", "")
        for m in st.messages
    )


def test_importing_agent_registers_builtin_tools():
    # Regression: a linter once removed agent's side-effect tools import, leaving
    # the registry empty so the model was offered NO tools and emitted calls as
    # text. Importing the agent must leave the built-ins registered.
    import drydock.agent  # noqa: F401
    from drydock.tool_registry import schemas

    names = {s["name"] for s in schemas()}
    assert {"Read", "Write", "Edit", "Bash"} <= names


def test_adaptive_reasoning_high_to_plan_then_low(monkeypatch):
    efforts = []

    def fake_stream(model, system, messages, tool_schemas, config):
        efforts.append(config.get("reasoning_effort"))
        if len(efforts) == 1:
            # planning turn → makes a tool call, forcing a continuation turn
            yield AssistantTurn("", [{"id": "1", "name": "Bash", "input": {"command": "ls"}}], 1, 1)
        else:
            yield AssistantTurn("done", [], 1, 1)

    monkeypatch.setattr(agent_mod, "stream", fake_stream)
    # execute() would run a real Bash; stub it to keep the test hermetic.
    monkeypatch.setattr(agent_mod, "execute", lambda name, inp, cfg: "ok")
    st = AgentState()
    list(run("go", st, {"model": "gemma4"}, "sys"))
    # First (planning) turn high, the continuation that consumes the tool result low.
    assert efforts == ["high", "low"]


def test_agent_retry_is_capped(monkeypatch):
    calls = {"n": 0}

    def always_leaks(model, system, messages, tool_schemas, config):
        calls["n"] += 1
        yield AssistantTurn("(blob)", [], 1, 1, had_leaked_call=True)

    monkeypatch.setattr(agent_mod, "stream", always_leaks)
    st = AgentState()
    list(run("build it", st, {"model": "gemma4"}, "sys"))
    # 1 initial + 2 retries, then it gives up — never an infinite loop.
    assert calls["n"] == 3

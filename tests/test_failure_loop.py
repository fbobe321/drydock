"""Safety valve: a byte-identical tool call that keeps FAILING must end the
turn after a small cap, not spin toward MAX_TOOL_TURNS (a real session failed
the same Write 160×)."""
from __future__ import annotations

import drydock.agent as agent_mod
from drydock.agent import AgentState, run
from drydock.providers import AssistantTurn


def test_identical_failure_streak_ends_turn(monkeypatch):
    calls = {"n": 0}

    def counting(**kw):
        calls["n"] += 1
        # Same failing Write every time (empty file_path → Error). id varies but
        # the input (the signature) is identical.
        return iter([AssistantTurn(
            "", [{"id": str(calls["n"]), "name": "Write",
                  "input": {"file_path": "", "content": "x"}}], 1, 1)])

    monkeypatch.setattr(agent_mod, "stream", counting)
    st = AgentState()
    list(run("build it", st, {"model": "m"}, "sys"))
    assert calls["n"] <= 12  # stopped near the cap (8), nowhere near 200


def test_changing_args_do_not_trip_valve(monkeypatch):
    # Legit iterative fixing (different args each time) must NOT trip the valve;
    # here each call succeeds (writes a different file), then the model finishes.
    seq = [
        AssistantTurn("", [{"id": "1", "name": "Write",
                            "input": {"file_path": "a.txt", "content": "1"}}], 1, 1),
        AssistantTurn("", [{"id": "2", "name": "Write",
                            "input": {"file_path": "b.txt", "content": "2"}}], 1, 1),
        AssistantTurn("done", [], 1, 1),
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    import tempfile
    list(run("go", st, {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False}, "sys"))
    assert any(m.get("role") == "assistant" and m.get("content") == "done"
               for m in st.messages)


def test_identical_SUCCESS_streak_ends_turn(monkeypatch):
    # The code-red case: a SUCCESSFUL command re-run identically (same args AND
    # same output) — a passing `pytest` 92×. Must also stop, not just failures.
    import tempfile
    calls = {"n": 0}

    def counting(**kw):
        calls["n"] += 1
        return iter([AssistantTurn(
            "", [{"id": str(calls["n"]), "name": "Bash",
                  "input": {"command": "echo hi"}}], 1, 1)])

    monkeypatch.setattr(agent_mod, "stream", counting)
    st = AgentState()
    list(run("go", st, {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False}, "sys"))
    assert calls["n"] <= 12  # identical success → stopped at the cap, not 200


def test_polling_with_changing_result_not_tripped(monkeypatch):
    # Same command, DIFFERENT result each time (a poll) must NOT be stopped —
    # the result changes, so the streak resets. Model finishes on its own.
    import tempfile
    seq = []
    for i in range(3):
        seq.append(AssistantTurn(
            "", [{"id": str(i), "name": "Bash",
                  "input": {"command": f"echo {i}"}}], 1, 1))  # different output
    seq.append(AssistantTurn("done", [], 1, 1))
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    list(run("go", st, {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False}, "sys"))
    assert any(m.get("content") == "done" for m in st.messages)


def test_infinite_distinct_tool_calls_terminate_at_max_turns(monkeypatch):
    """PRD §3.D: a model that keeps calling tools (DIFFERENT, succeeding calls)
    and never yields 'Task Complete' must be forcibly stopped at the hard
    max-iteration ceiling — protecting VRAM/context from an unbounded loop."""
    calls = {"n": 0}

    def never_done(**kw):
        calls["n"] += 1
        # A unique, succeeding command each turn → the identical-repeat valve
        # never trips, so only the hard max_turns cap can end it.
        return iter([AssistantTurn(
            "", [{"id": str(calls["n"]), "name": "Bash",
                  "input": {"command": f"echo step {calls['n']}"}}], 1, 1)])

    monkeypatch.setattr(agent_mod, "stream", never_done)
    st = AgentState()
    import tempfile
    # Low cap so the test is fast; the real default is 200.
    list(run("loop forever", st, {"model": "m", "max_turns": 12,
                                  "cwd": tempfile.mkdtemp()}, "sys"))
    assert calls["n"] == 12  # ran exactly to the ceiling, then terminated

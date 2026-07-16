"""Regression for the dna-insert bench finding (PRD Epic J3): the SAME failing
verification outcome recurring through DIFFERENT commands must accrue toward
recovery — no exact call repeats, so repeat_count alone never fires."""
from __future__ import annotations

import tempfile

import drydock.agent as agent_mod
from drydock.agent import AgentState, run
from drydock.providers import AssistantTurn


def _varying_failing_verifications():
    """Model that re-runs a failing check with a slightly different command each
    time (comment varies) — same failing outcome, no byte-identical repeats."""
    def stream(**kw):
        n = stream.n
        stream.n += 1
        cmd = f'python3 -m pytest nonexistent_{0}.py  # attempt {n}'
        return iter([AssistantTurn(
            "", [{"id": str(n), "name": "Bash", "input": {"command": cmd}}], 1, 1)])
    stream.n = 0
    return stream


def test_same_failing_outcome_different_commands_triggers_recovery(monkeypatch):
    monkeypatch.setattr(agent_mod, "stream", _varying_failing_verifications())
    st = AgentState()
    list(run("make the tests pass", st,
             {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False,
              "max_turns": 60}, "sys"))
    # The identical failing outcome (same exit/summary through varying commands)
    # must drive recovery well short of the 60-turn ceiling.
    assert st.turn_count < 30, f"ran {st.turn_count} turns — repeated outcome never fired"
    assert st.recovery_stage >= 3


def test_changing_failure_does_not_count_as_repeated_outcome(monkeypatch):
    # Failures that CHANGE (different summary each time) are progress signals,
    # not stalls — a healthy fix-test-fix loop must not trip J3.
    seq = []
    for i in range(1, 5):
        # each command exits with a DIFFERENT code -> different summary key
        seq.append(AssistantTurn(
            "", [{"id": str(i), "name": "Bash",
                  "input": {"command": f'python3 -m pytest x.py; exit {i}  # run'}}], 1, 1))
    seq.append(AssistantTurn("done for now", [], 1, 1))
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    list(run("check things", st,
             {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False}, "sys"))
    # ran to its natural end without recovery escalating past advisory
    assert any(m.get("content") == "done for now" for m in st.messages)
    assert st.recovery_stage <= 1

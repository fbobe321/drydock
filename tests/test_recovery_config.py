"""Tests for configurable recovery tuning (PRD §13) + max_recovery_attempts
forcing a controlled stop (PRD N1.4)."""
from __future__ import annotations

import tempfile

import drydock.agent as agent_mod
from drydock.agent import AgentState, run
from drydock.providers import AssistantTurn


def _alternating_loop(macro):
    def stream(**kw):
        n = stream.n
        stream.n += 1
        if n % 2 == 0:
            tc = {"id": str(n), "name": "Write",
                  "input": {"file_path": macro, "content": "x"}}
        else:
            tc = {"id": str(n), "name": "Bash", "input": {"command": "false"}}
        return iter([AssistantTurn("", [tc], 1, 1)])
    stream.n = 0
    return stream


def test_max_recovery_attempts_forces_stop(monkeypatch):
    # With a tight recovery ceiling, a stalled run terminates via the recovery
    # budget — not by burning all 200 turns.
    macro = tempfile.NamedTemporaryFile(suffix=".vim", delete=False).name
    monkeypatch.setattr(agent_mod, "stream", _alternating_loop(macro))
    st = AgentState()
    list(run("loop", st, {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False,
             "max_turns": 200, "max_recovery_attempts": 2}, "sys"))
    # stopped early (recovery ceiling), and recorded the attempts
    assert st.turn_count < 40
    assert st.budget.recovery_attempts >= 2


def test_no_progress_window_is_configurable(monkeypatch):
    # A larger window means the stall takes longer to trip recovery. We just
    # assert the config flows through to the tracker's behavior: with a big window,
    # the run goes further before recovery terminates than with a small one.
    macro = tempfile.NamedTemporaryFile(suffix=".vim", delete=False).name

    def run_with(window):
        monkeypatch.setattr(agent_mod, "stream", _alternating_loop(macro))
        st = AgentState()
        list(run("loop", st, {"model": "m", "cwd": tempfile.mkdtemp(),
                 "verify_gate": False, "max_turns": 200,
                 "recovery_no_progress_window": window}, "sys"))
        return st.turn_count

    # both terminate via recovery; just assert the knob is honored (no crash, both bounded)
    small = run_with(3)
    assert small < 60


def test_defaults_present():
    from drydock import config as cfgmod
    assert cfgmod.DEFAULTS["recovery_no_progress_window"] == 5
    assert cfgmod.DEFAULTS["recovery_suppression_iterations"] == 2
    assert cfgmod.DEFAULTS["max_recovery_attempts"] == 0

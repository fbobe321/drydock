"""Tests for scoped budgets (PRD Epic N): request resets, session is cumulative,
tool-call budget exhaustion."""
from __future__ import annotations

import tempfile

import drydock.agent as agent_mod
from drydock.agent import AgentState, run
from drydock.budget import BudgetState
from drydock.providers import AssistantTurn


def test_request_counters_reset_but_session_is_cumulative():
    # PRD N1.1 / N1.2
    b = BudgetState(max_model_iterations=50)
    for _ in range(20):
        b.record_turn()
    assert b.request_iterations == 20 and b.session_turns == 20
    b.start_request()
    assert b.request_iterations == 0        # request resets
    assert b.session_turns == 20            # session stays cumulative


def test_iterations_exhausted():
    b = BudgetState(max_model_iterations=3)
    for _ in range(3):
        assert not b.iterations_exhausted()
        b.record_turn()
    assert b.iterations_exhausted()


def test_tool_budget_exhaustion():
    # PRD N1.3
    b = BudgetState(max_tool_calls=2)
    b.record_tool_call(); assert not b.tool_budget_exhausted()
    b.record_tool_call(); assert not b.tool_budget_exhausted()
    b.record_tool_call(); assert b.tool_budget_exhausted()


def test_zero_tool_budget_is_unlimited():
    b = BudgetState(max_tool_calls=0)
    for _ in range(100):
        b.record_tool_call()
    assert not b.tool_budget_exhausted()


def test_recovery_budget():
    b = BudgetState(max_recovery_attempts=2)
    b.record_recovery(); assert not b.recovery_exhausted()
    b.record_recovery(); assert b.recovery_exhausted()


def test_second_request_gets_full_iteration_budget(monkeypatch):
    # The latent-bug fix: a second user message on the SAME session must get a
    # full per-request iteration budget, not the leftovers of a cumulative cap.
    def infinite(**kw):
        return iter([AssistantTurn(
            "", [{"id": "1", "name": "Bash", "input": {"command": "echo step"}}], 1, 1)])
    monkeypatch.setattr(agent_mod, "stream", infinite)
    st = AgentState()
    cfg = {"model": "m", "max_turns": 6, "cwd": tempfile.mkdtemp(), "verify_gate": False}

    list(run("first task", st, cfg, "sys"))
    first = st.budget.request_iterations
    list(run("second task", st, cfg, "sys"))
    second = st.budget.request_iterations
    # both requests ran a full budget's worth (~6), even though session_turns grew
    assert first >= 6 and second >= 6
    assert st.budget.session_turns >= first + second - 1  # cumulative across both

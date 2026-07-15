"""Tests for explicit agent phases (PRD Epic B): the controller owns transitions,
completion needs verification evidence, and the enum stays string-compatible."""
from __future__ import annotations

from drydock.phases import AgentPhase, PhaseController, as_phase


def test_phase_enum_is_string_compatible():
    # str-Enum: members compare equal to the plain strings the codebase uses.
    assert AgentPhase.VERIFY == "verify"
    assert AgentPhase.UNDERSTAND in ("understand", "discover", "plan")
    assert f"{AgentPhase.COMPLETE}" == "complete"


def test_simple_task_phase_sequence():
    # PRD Test B1.1: UNDERSTAND -> IMPLEMENT -> VERIFY -> COMPLETE
    pc = PhaseController()
    p = AgentPhase.UNDERSTAND
    p = pc.advance(p, AgentPhase.IMPLEMENT)
    assert p == AgentPhase.IMPLEMENT
    p = pc.advance(p, AgentPhase.VERIFY)
    assert p == AgentPhase.VERIFY
    p = pc.advance(p, AgentPhase.COMPLETE, verified=True)
    assert p == AgentPhase.COMPLETE


def test_model_cannot_self_declare_completion():
    # PRD Test B1.4: a completion claim without verification evidence is routed
    # to VERIFY, not accepted.
    pc = PhaseController()
    p = pc.advance(AgentPhase.IMPLEMENT, AgentPhase.COMPLETE, verified=False)
    assert p == AgentPhase.VERIFY


def test_complete_only_reachable_from_verify():
    pc = PhaseController()
    # even "verified" completion is only granted from VERIFY
    assert pc.advance(AgentPhase.IMPLEMENT, AgentPhase.COMPLETE, verified=True) != AgentPhase.COMPLETE
    assert pc.advance(AgentPhase.VERIFY, AgentPhase.COMPLETE, verified=True) == AgentPhase.COMPLETE


def test_failed_verification_can_go_to_repair():
    # PRD Test B1.3
    pc = PhaseController()
    assert pc.advance(AgentPhase.VERIFY, AgentPhase.REPAIR) == AgentPhase.REPAIR


def test_invalid_transition_stays_put():
    pc = PhaseController()
    # UNDERSTAND -> VERIFY is not a legal jump; stay in UNDERSTAND
    assert pc.advance(AgentPhase.UNDERSTAND, AgentPhase.VERIFY) == AgentPhase.UNDERSTAND


def test_terminal_phases_do_not_transition():
    pc = PhaseController()
    assert pc.is_terminal(AgentPhase.COMPLETE)
    assert pc.is_terminal(AgentPhase.FAILED)
    assert pc.advance(AgentPhase.COMPLETE, AgentPhase.IMPLEMENT) == AgentPhase.COMPLETE


def test_any_phase_can_be_blocked_or_cancelled():
    pc = PhaseController()
    assert pc.advance(AgentPhase.IMPLEMENT, AgentPhase.BLOCKED) == AgentPhase.BLOCKED
    assert pc.advance(AgentPhase.DISCOVER, AgentPhase.CANCELLED) == AgentPhase.CANCELLED


def test_as_phase_coerces_unknown_to_understand():
    assert as_phase("verify") == AgentPhase.VERIFY
    assert as_phase("nonsense") == AgentPhase.UNDERSTAND
    assert as_phase(AgentPhase.REPAIR) == AgentPhase.REPAIR


def test_same_phase_is_a_noop():
    pc = PhaseController()
    assert pc.advance(AgentPhase.IMPLEMENT, AgentPhase.IMPLEMENT) == AgentPhase.IMPLEMENT

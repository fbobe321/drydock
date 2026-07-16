"""Tests for recovery escalation (PRD Epic K): graduated stages, narrow
self-expiring suppression, and honest termination — all advisory (a directive
is returned, the loop is never hard-stopped)."""
from __future__ import annotations

from drydock.recovery import RecoveryController, RecoveryDirective


def test_no_recommendation_is_stage_zero():
    rc = RecoveryController()
    d = rc.escalate(None, offender_signature="Bash\x00x", iteration=1)
    assert d.stage == 0
    assert d.active is False
    assert d.suppress is False


def test_stage_one_is_advisory_only():
    rc = RecoveryController()
    d = rc.escalate(1, offender_signature="Bash\x00x", iteration=1)
    assert d.stage == 1
    assert d.note and "progress" in d.note.lower()
    assert d.suppress is False
    assert d.terminate is False


def test_stage_two_demands_reflection():
    rc = RecoveryController()
    d = rc.escalate(2, offender_signature=None, iteration=1)
    assert d.stage == 2
    assert "REFLECT" in d.note


def test_stage_three_suppresses_the_offender():
    rc = RecoveryController()
    sig = "Bash\x00{'command': 'pytest'}"
    d = rc.escalate(3, offender_signature=sig, iteration=5)
    assert d.stage == 3
    assert d.suppress is True
    assert rc.is_suppressed(sig) is True


def test_suppression_is_narrow_and_expires():
    rc = RecoveryController(suppression_iterations=2)
    sig = "Bash\x00pytest"
    rc.escalate(3, offender_signature=sig, iteration=5)
    # a DIFFERENT action is never suppressed
    assert rc.is_suppressed("Read\x00a.py") is False
    # still suppressed within the window...
    rc.tick(6)
    assert rc.is_suppressed(sig) is True
    # ...expires after the configured number of iterations
    rc.tick(7)
    assert rc.is_suppressed(sig) is False


def test_suppression_message_names_the_tool_and_is_a_string():
    rc = RecoveryController()
    msg = rc.suppression_message("Bash")
    assert isinstance(msg, str)
    assert "Bash" in msg
    assert "different" in msg.lower()


def test_stage_five_terminates_honestly():
    rc = RecoveryController()
    d = rc.escalate(5, offender_signature=None, iteration=9)
    assert d.stage == 5
    assert d.terminate is True
    assert "honest" in d.note.lower() or "not verified" in d.note.lower() \
        or "complete" in d.note.lower()


def test_escalation_is_monotonic_until_progress():
    rc = RecoveryController()
    rc.escalate(3, offender_signature="s", iteration=1)
    assert rc.stage == 3
    # a lower recommendation must NOT downgrade an in-flight recovery
    d = rc.escalate(1, offender_signature="s", iteration=2)
    assert d.stage == 3
    # ...but real progress (None) resets to stage 0
    d = rc.escalate(None, offender_signature=None, iteration=3)
    assert d.stage == 0
    assert rc.stage == 0


def test_second_fresh_suppression_of_same_signature_terminates():
    # PRD K1.6: the first suppression ran its course and the model came straight
    # back to the same call — suppress-expire-repeat forever is dishonest; stop.
    rc = RecoveryController(suppression_iterations=2)
    d1 = rc.escalate(3, offender_signature="sig", iteration=1)
    assert d1.stage == 3 and not d1.terminate
    # window refresh while still suppressed does NOT count as a second arming
    d2 = rc.escalate(3, offender_signature="sig", iteration=2)
    assert not d2.terminate
    rc.tick(10)  # suppression expires
    d3 = rc.escalate(3, offender_signature="sig", iteration=10)  # fresh re-arm
    assert d3.stage == 5 and d3.terminate is True


def test_directive_dataclass_defaults():
    d = RecoveryDirective()
    assert d.stage == 0 and d.note is None and not d.suppress and not d.terminate

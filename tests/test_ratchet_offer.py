"""The proactive /ratchet offer: fires only when the model is genuinely stuck on a
repeated failing check, discloses the rollback cost, and never nags."""
from __future__ import annotations

from drydock.agent import AgentState
from drydock.ratchet import OFFER_AFTER_FAILURES, ratchet_offer


def test_no_offer_before_threshold():
    for n in range(OFFER_AFTER_FAILURES):
        assert ratchet_offer("fix the suite", "pytest -q", n) is None


def test_offer_at_threshold_names_the_command_and_goal():
    o = ratchet_offer("fix the failing suite", "pytest -q", OFFER_AFTER_FAILURES)
    assert o and "pytest -q" in o and "fix the failing suite" in o


def test_offer_discloses_the_rollback_cost():
    """Consent needs the cost: the ratchet can rewind uncommitted work."""
    o = ratchet_offer("g", "make test", 5)
    assert "REWIND" in o and "git" in o.lower()


def test_no_offer_without_a_verifier():
    """Nothing to climb -> nothing to offer, however many failures."""
    assert ratchet_offer("g", "", 99) is None


def test_long_goal_is_truncated_not_dumped():
    o = ratchet_offer("x" * 500, "pytest", 3)
    assert o is not None and "x" * 200 not in o


def test_state_tracks_the_failing_streak():
    s = AgentState()
    assert s.verify_fail_streak == 0 and s.last_verify_cmd == ""
    s.verify_fail_streak = 3
    assert ratchet_offer("g", "pytest", s.verify_fail_streak) is not None


def test_streak_increments_on_fail_and_RESETS_on_pass():
    """Mirrors the agent.py path. A pass MUST reset, else a stale run of old failures
    would trigger the offer long after the model recovered."""
    from drydock.verification import parse_evidence
    s = AgentState()

    def observe(cmd, out):
        ev = parse_evidence(cmd, out)
        if ev.status == "fail":
            s.verify_fail_streak += 1
            s.last_verify_cmd = cmd
        elif ev.status == "pass":
            s.verify_fail_streak = 0
        return ev.status

    assert observe("pytest -q", "1 failed, 2 passed\n[exit code: 1]") == "fail"
    observe("pytest -q", "1 failed, 2 passed\n[exit code: 1]")
    observe("pytest -q", "1 failed, 2 passed\n[exit code: 1]")
    assert s.verify_fail_streak == 3
    assert ratchet_offer("g", s.last_verify_cmd, s.verify_fail_streak) is not None
    assert observe("pytest -q", "3 passed\n[exit code: 0]") == "pass"
    assert s.verify_fail_streak == 0
    assert ratchet_offer("g", s.last_verify_cmd, s.verify_fail_streak) is None

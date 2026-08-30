"""Lock in the first-principles reflection shipped 2026-08-30.

These assert the MEASURED-BETTER content, so a future reword cannot silently drop the
two questions that carried the gain (assumption-checking and the cheapest test), nor the
"concrete criteria" instruction whose compliance tracked the win.
"""
from drydock.recovery import RecoveryController
from drydock.ratchet import diversify_prompt


def _stage2_note():
    g = RecoveryController()
    return g.escalate(2, offender_signature="Bash\x00x", iteration=1).note or ""


def test_stage2_asks_for_assumptions_and_cheapest_test():
    n = _stage2_note().lower()
    assert "assuming" in n, "assumption-check is the highest-value question; do not drop it"
    assert "prevents" in n, "bottleneck identification missing"
    assert "simplest" in n, "cheapest-experiment step missing"
    assert "verified" in n, "must distinguish verified facts from assumptions"


def test_stage2_demands_concrete_criteria_not_prose():
    n = _stage2_note().lower()
    assert "concrete" in n or "checkable" in n
    assert "not prose" in n or "exact" in n


def test_stage2_stays_advisory_and_does_not_terminate():
    d = RecoveryController().escalate(2, offender_signature="Bash\x00x", iteration=1)
    assert d.active and not d.terminate and not d.suppress


def test_stage2_stays_brief():
    # A 10-step variant was FOLLOWED LESS (criteria written 3/8 vs 7/8). Brevity is
    # load-bearing, so fail if this note grows unboundedly.
    assert len(_stage2_note()) < 1200


def test_ratchet_restart_is_explicit_not_aspirational():
    p = diversify_prompt("goal", 2, 5, "restart").lower()
    assert "assuming" in p and "simplest" in p and "prevents" in p
    assert "re-word" in p or "reword" in p, "must forbid re-wording the previous plan"
    assert "keep everything that already passes" in p, "pawl semantics must survive"


def test_ratchet_diversify_unchanged_still_varies_strategy():
    p = diversify_prompt("goal", 2, 5, "diversify").lower()
    assert "stalled" in p and "different" in p

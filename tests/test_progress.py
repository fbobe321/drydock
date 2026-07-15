"""Tests for progress evaluation (PRD Epic L): scoring the score table, and the
sliding window that recommends a recovery stage once a run has stalled."""
from __future__ import annotations

from drydock.progress import (
    NO_PROGRESS_WINDOW,
    ProgressTracker,
    assess_action,
)


# --- score table (PRD §L / §8.2) ---

def test_state_change_scores_positive():
    a = assess_action(changed_state=True)
    assert a.progress_score == 2
    assert a.made_progress is True
    assert "state_changed" in a.progress_types


def test_discovery_counts_as_progress_without_repo_change():
    # PRD Test J4.2 / L1.1: finding a new relevant fact is progress even when the
    # repository has not changed.
    a = assess_action(changed_state=False, discovered_new=True)
    assert a.progress_score == 2
    assert a.made_progress is True
    assert "discovery" in a.progress_types


def test_criterion_satisfied_scores_three():
    a = assess_action(criterion_satisfied=True)
    assert a.progress_score == 3
    assert "criterion_satisfied" in a.progress_types


def test_failing_test_resolved_scores_three():
    # PRD Test J4.4-adjacent: all failures cleared.
    a = assess_action(failing_tests_before=4, failing_tests_after=0)
    assert a.progress_score == 3
    assert "failing_test_resolved" in a.progress_types


def test_reduced_failures_counts_as_verification_progress():
    # PRD Test J4.4: 4 failing -> 2 failing is progress, but not a full resolve.
    a = assess_action(failing_tests_before=4, failing_tests_after=2)
    assert a.progress_score == 2
    assert "verification_progress" in a.progress_types


def test_neutral_operation_scores_zero():
    a = assess_action()
    assert a.progress_score == 0
    assert a.made_progress is False


def test_repeated_action_without_new_info_penalized():
    # PRD Test L1.2: an exact repeat that changes nothing is a repetition penalty.
    a = assess_action(repeat_count=3, changed_state=False)
    assert a.progress_score < 0
    assert a.exact_repeat_detected is True
    assert "repeated_action" in a.progress_types


def test_repeated_outcome_penalized():
    a = assess_action(repeated_outcome=True)
    assert a.progress_score == -1
    assert a.repeated_outcome_detected is True


def test_edit_reversal_is_negative_progress():
    # PRD Test J4.3: an edit that restores prior contents is negative progress and
    # must NOT be rewarded for "changing state".
    a = assess_action(changed_state=True, edit_reversal=True)
    assert a.progress_score == -3
    assert a.edit_reversal_detected is True
    assert "state_changed" not in a.progress_types


def test_diagnostic_scores_one():
    a = assess_action(diagnostic=True)
    assert a.progress_score == 1


def test_identical_repeated_write_is_not_progress():
    # The masking bug: a byte-identical re-write reports changed_state=True but
    # writes the same bytes — it must NOT score as progress, and IS a stall.
    first = assess_action(changed_state=True, repeat_count=1)
    again = assess_action(changed_state=True, repeat_count=3)
    assert first.progress_score == 2          # the genuine first write
    assert again.progress_score < 0           # re-writing identical bytes: stall
    assert "state_changed" not in again.progress_types


def test_distinct_edits_each_score_progress():
    # Legit iterative editing changes its arguments, so repeat_count stays 1 and
    # every distinct edit is still credited.
    for _ in range(4):
        a = assess_action(changed_state=True, repeat_count=1)
        assert a.progress_score == 2


# --- sliding window (PRD Req L1: progress window triggers recovery) ---

def _stall():
    """A repetitive no-progress action (exact repeat, no state change) — the
    kind that should accrue toward recovery."""
    return assess_action(repeat_count=2, changed_state=False)


def test_neutral_novel_actions_do_not_trigger_recovery():
    # The key false-positive guard: distinct succeeding/exploring actions score 0
    # but are NOT a loop, so they must never trip recovery no matter how many.
    t = ProgressTracker(window=5)
    for _ in range(10):
        t.record(assess_action())  # neutral, non-repetitive
    assert t.no_progress_streak() == 0
    assert t.should_recover() is False
    assert t.recommended_stage() is None


def test_stall_streak_triggers_recovery():
    # PRD Test L1.3: a run genuinely looping (repetitive no-progress actions)
    # recommends recovery.
    t = ProgressTracker()
    for _ in range(5):
        t.record(_stall())
    assert t.should_recover() is True
    assert t.recommended_stage() is not None


def test_progress_resets_the_stall_streak():
    t = ProgressTracker()
    t.record(_stall())
    t.record(_stall())
    assert t.no_progress_streak() == 2
    t.record(assess_action(changed_state=True))  # +2 real progress
    assert t.no_progress_streak() == 0
    assert t.should_recover() is False


def test_novel_action_resets_the_stall_streak():
    # A stall streak is broken by a novel action, not just by positive progress.
    t = ProgressTracker()
    t.record(_stall())
    t.record(_stall())
    assert t.no_progress_streak() == 2
    t.record(assess_action())  # neutral novel — interrupts the loop
    assert t.no_progress_streak() == 0


def test_recommended_stage_escalates_with_stall_streak():
    # PRD §13 thresholds: stage 1 after 2, stage 2 after 3, stage 3 after 4...
    t = ProgressTracker(window=20)
    t.record(_stall())
    assert t.recommended_stage() is None            # streak 1, below stage 1
    t.record(_stall())
    assert t.recommended_stage() == 1               # streak 2
    t.record(_stall())
    assert t.recommended_stage() == 2               # streak 3
    t.record(_stall())
    assert t.recommended_stage() == 3               # streak 4


def test_edit_reversal_counts_toward_recovery():
    t = ProgressTracker()
    t.record(assess_action(changed_state=True, edit_reversal=True))  # -3 stall
    t.record(assess_action(changed_state=True, edit_reversal=True))
    assert t.no_progress_streak() == 2
    assert t.recommended_stage() == 1


def test_default_window_matches_prd_config():
    assert NO_PROGRESS_WINDOW == 5

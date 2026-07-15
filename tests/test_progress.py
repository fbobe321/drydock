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


# --- sliding window (PRD Req L1: progress window triggers recovery) ---

def test_window_not_full_does_not_recover():
    t = ProgressTracker(window=5)
    for _ in range(4):
        t.record(assess_action())  # zero-score actions
    assert t.should_recover() is False  # window not yet full


def test_full_window_of_no_progress_triggers_recovery():
    # PRD Test L1.3: cumulative score across the window is zero -> recommend recovery.
    t = ProgressTracker(window=5, threshold=0)
    for _ in range(5):
        t.record(assess_action())
    assert t.cumulative() == 0
    assert t.should_recover() is True
    assert t.recommended_stage() is not None


def test_progress_resets_the_window_verdict():
    t = ProgressTracker(window=3, threshold=0)
    t.record(assess_action())                       # 0
    t.record(assess_action())                       # 0
    t.record(assess_action(changed_state=True))     # +2 -> cumulative 2
    assert t.cumulative() == 2
    assert t.should_recover() is False


def test_progress_clears_the_no_progress_streak():
    t = ProgressTracker(window=5)
    for _ in range(4):
        t.record(assess_action())
    assert t.no_progress_streak() == 4
    t.record(assess_action(changed_state=True))  # real progress
    assert t.no_progress_streak() == 0


def test_recommended_stage_escalates_with_streak():
    # PRD §13 thresholds: stage 1 after 2, stage 2 after 3, stage 3 after 4...
    t = ProgressTracker(window=20)
    t.record(assess_action())
    assert t.recommended_stage() is None            # streak 1, below stage 1
    t.record(assess_action())
    assert t.recommended_stage() == 1               # streak 2
    t.record(assess_action())
    assert t.recommended_stage() == 2               # streak 3
    t.record(assess_action())
    assert t.recommended_stage() == 3               # streak 4


def test_negative_window_triggers_recovery():
    t = ProgressTracker(window=3, threshold=0)
    t.record(assess_action(repeat_count=2))         # -2
    t.record(assess_action(repeated_outcome=True))  # -1
    t.record(assess_action())                       # 0
    assert t.cumulative() < 0
    assert t.should_recover() is True


def test_default_window_matches_prd_config():
    assert NO_PROGRESS_WINDOW == 5

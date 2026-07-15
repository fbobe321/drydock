"""Progress evaluation — score each action, notice when the agent has stopped
getting anywhere, and recommend a recovery stage before it burns the whole
turn budget spinning.

This is the missing decision layer the PRD's central thesis hangs on ("the
model proposes; the harness governs"): loop detection (loop_detect.py) already
notices *exact* repeats, but a run can stall without any single call repeating —
re-reading different slices of the same file, rerunning tests that keep failing,
editing and un-editing. Progress scoring (PRD Epic L) turns each action's
outcome into a signed score, and a sliding window over those scores answers the
question loop detection can't: "are we actually making progress, or just moving?"

The score table is taken directly from the PRD:

    +3  acceptance criterion satisfied
    +3  failing test resolved  (all failures cleared)
    +2  repository state changed meaningfully
    +2  reduced test failures  (fewer failing than before)
    +2  new relevant file or symbol discovered
    +1  useful diagnostic discovered
     0  neutral operation
    -1  repeated equivalent result
    -2  repeated action without new information
    -3  immediate edit reversal

Pure and stdlib-only. Assessment never raises; the window is advisory — it
recommends a recovery stage, it does not stop the loop. All logic original to
Drydock.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

# Recovery escalation thresholds (PRD §13 "Recovery Configuration"). A no-progress
# streak of this many actions maps to the given stage; Epic K (recovery escalation)
# consumes these, Epic L only *recommends*. Kept here so the mapping has one home
# until the recovery controller lands.
STAGE_AFTER = {1: 2, 2: 3, 3: 4, 4: 6, 5: 8}

# Sliding-window defaults (PRD §13: no_progress_window / no_progress_score_threshold).
NO_PROGRESS_WINDOW = 5
NO_PROGRESS_SCORE_THRESHOLD = 0


@dataclass
class ProgressAssessment:
    """The outcome of scoring one action (PRD 7.6, trimmed to what the loop can
    actually observe at the tool-execution seam)."""
    progress_score: int = 0
    made_progress: bool = False
    progress_types: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    exact_repeat_detected: bool = False
    repeated_outcome_detected: bool = False
    no_state_change_detected: bool = False
    edit_reversal_detected: bool = False

    def to_dict(self) -> dict:
        return {
            "progress_score": self.progress_score,
            "made_progress": self.made_progress,
            "progress_types": list(self.progress_types),
            "exact_repeat": self.exact_repeat_detected,
            "repeated_outcome": self.repeated_outcome_detected,
            "edit_reversal": self.edit_reversal_detected,
        }


def assess_action(
    *,
    changed_state: bool = False,
    repeat_count: int = 1,
    discovered_new: bool = False,
    diagnostic: bool = False,
    criterion_satisfied: bool = False,
    failing_tests_before: int | None = None,
    failing_tests_after: int | None = None,
    edit_reversal: bool = False,
    repeated_outcome: bool = False,
) -> ProgressAssessment:
    """Score a single action from observable signals at the tool seam.

    Contributions are summed (an action can both change state AND discover a
    fact), then edit-reversal — the one signal that means "we went backwards" —
    applies its penalty on top. All inputs are primitives so this is trivially
    testable in isolation; the agent loop fills them from the ToolResult,
    LoopTracker count and VerificationEvidence it already has.
    """
    score = 0
    types: list[str] = []
    evidence: list[str] = []

    # --- verification progress: resolving or reducing failures ---
    if failing_tests_before is not None and failing_tests_after is not None:
        if failing_tests_before > 0 and failing_tests_after == 0:
            score += 3
            types.append("failing_test_resolved")
            evidence.append(f"all {failing_tests_before} failing test(s) now pass")
        elif failing_tests_after < failing_tests_before:
            score += 2
            types.append("verification_progress")
            evidence.append(
                f"failing tests {failing_tests_before} -> {failing_tests_after}"
            )

    if criterion_satisfied:
        score += 3
        types.append("criterion_satisfied")
        evidence.append("acceptance criterion satisfied")

    # --- repository / information progress ---
    exact_repeat = repeat_count >= 2
    # A byte-identical repeat of a mutating call (same file, same content) writes
    # the same bytes again — the repository does NOT actually change relative to
    # before, so it must not be credited as progress (this is what let a model
    # re-write an identical macro 26× and still look busy). A genuine iterative
    # edit changes its arguments, so its signature differs and repeat_count stays 1.
    effective_change = changed_state and not edit_reversal and not exact_repeat
    if effective_change:
        score += 2
        types.append("state_changed")
        evidence.append("repository state changed")
    if discovered_new:
        score += 2
        types.append("discovery")
        evidence.append("new relevant file/symbol discovered")
    if diagnostic:
        score += 1
        types.append("diagnostic")
        evidence.append("useful diagnostic surfaced")

    # --- penalties: standing still or going backwards ---
    # "no new info" = nothing above earned any points (no state change, discovery,
    # diagnostic, verification or criterion progress). score holds only positive
    # contributions at this point; penalties are applied below.
    no_new_info = score <= 0 and not (effective_change or discovered_new or diagnostic)
    if repeated_outcome:
        score -= 1
        types.append("repeated_outcome")
    if exact_repeat and no_new_info:
        score -= 2
        types.append("repeated_action")
    if edit_reversal:
        score -= 3
        types.append("edit_reversal")
        evidence.append("edit restored prior contents")

    return ProgressAssessment(
        progress_score=score,
        made_progress=score > 0,
        progress_types=types,
        evidence=evidence,
        exact_repeat_detected=exact_repeat,
        repeated_outcome_detected=repeated_outcome,
        no_state_change_detected=not changed_state,
        edit_reversal_detected=edit_reversal,
    )


def is_stall(a: ProgressAssessment) -> bool:
    """A stall is a non-positive action that is also REPETITIVE — the model
    going in circles, not exploring. Neutral novel actions (score 0, no repeat
    signal) are NOT stalls, so a run reading many distinct files or trying many
    distinct commands is never mistaken for a loop."""
    return a.progress_score <= 0 and (
        a.exact_repeat_detected
        or a.repeated_outcome_detected
        or a.edit_reversal_detected
    )


class ProgressTracker:
    """A sliding window over per-action progress scores (PRD Epic L, Req L1).

    When the cumulative score across the last ``window`` actions is at or below
    ``threshold`` the run is stalled: should_recover() is True and
    recommended_stage() maps the length of the current no-progress streak onto a
    recovery stage. Purely advisory — it informs the recovery controller, it
    never stops the loop itself.
    """

    def __init__(
        self,
        window: int = NO_PROGRESS_WINDOW,
        threshold: int = NO_PROGRESS_SCORE_THRESHOLD,
    ) -> None:
        self.window = max(1, window)
        self.threshold = threshold
        self._scores: deque[int] = deque(maxlen=self.window)
        self._no_progress_streak = 0

    def record(self, assessment: ProgressAssessment) -> ProgressAssessment:
        """Push an assessment's score into the window; returns it unchanged so
        callers can `a = tracker.record(assess_action(...))` in one line.

        The no-progress streak — what drives recovery — counts only genuine
        STALLS: a non-positive action that is also REPETITIVE (an exact repeat,
        a repeated outcome, or an edit reversal). A neutral *novel* action
        (exploring, reading a new file, a distinct succeeding command) is not a
        stall and resets the streak, so legitimate exploration is never mistaken
        for a loop."""
        self._scores.append(assessment.progress_score)
        if is_stall(assessment):
            self._no_progress_streak += 1
        else:
            self._no_progress_streak = 0
        return assessment

    def cumulative(self) -> int:
        return sum(self._scores)

    def no_progress_streak(self) -> int:
        return self._no_progress_streak

    def should_recover(self) -> bool:
        """True once a stall streak has reached the first recovery threshold — i.e.
        the run is genuinely looping, not merely exploring. Neutral novel actions
        never trip this."""
        return self.recommended_stage() is not None

    def recommended_stage(self) -> int | None:
        """Map the current no-progress streak to a recovery stage (0 = normal).
        Returns the HIGHEST stage whose threshold the streak has reached, or None
        when we're still making headway. Epic K owns what each stage *does*."""
        stage = None
        for s, after in sorted(STAGE_AFTER.items()):
            if self._no_progress_streak >= after:
                stage = s
        return stage

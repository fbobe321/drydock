"""Recovery escalation (PRD Epic K) — graduated response to a stalled run.

Loop detection notices repetition and progress evaluation (progress.py) scores
whether a run is getting anywhere; this module is what the harness DOES about it.
Drydock never terminates at the first repeated action — it escalates through
stages of increasing firmness, so a model that's briefly spinning gets a nudge
while one that's truly stuck gets pulled off the plateau:

    Stage 0  normal operation
    Stage 1  advisory reminder            (name the repeated action)
    Stage 2  structured reflection         (make the model restate goal + a new plan)
    Stage 3  temporary action suppression  (the looping call returns a redirect,
                                            not its result, until state changes)
    Stage 4  strategy / context reset       (strong "abandon this approach" note)
    Stage 5  controlled termination         (honest stop — no false success claim)

Crucially, this stays within Drydock's advisory-first contract: even the strong
stages only ever RETURN a guidance string (never raise, never hard-block), and
suppression is a narrow, self-expiring redirect of one exact looping signature —
unrelated actions are always available. Consumes ProgressTracker.recommended_stage().
All logic original to Drydock.
"""
from __future__ import annotations

from dataclasses import dataclass

# How many iterations a suppressed signature stays suppressed (PRD §13:
# temporary_suppression_iterations = 2).
SUPPRESSION_ITERATIONS = 2

STAGE_NAMES = {
    0: "normal",
    1: "advisory",
    2: "reflection",
    3: "suppression",
    4: "strategy_reset",
    5: "termination",
}


@dataclass
class RecoveryDirective:
    """What the loop should do about the current action, given the stage."""
    stage: int = 0
    note: str | None = None          # guidance to inject into the next context
    suppress: bool = False           # short-circuit THIS action with `note`
    terminate: bool = False          # stage 5: stop the run honestly

    @property
    def active(self) -> bool:
        return self.stage > 0


class RecoveryController:
    """Owns the current recovery stage and the set of temporarily-suppressed
    action signatures. Advisory: it hands back a RecoveryDirective; it never
    stops the loop itself."""

    def __init__(self, suppression_iterations: int = SUPPRESSION_ITERATIONS) -> None:
        self.stage = 0
        self.suppression_iterations = max(1, suppression_iterations)
        # signature -> iteration index at which the suppression expires
        self._suppressed: dict[str, int] = {}
        # signature -> how many times it has EVER been suppressed this run; a
        # second arming means suppression didn't change the model's behavior —
        # per the PRD that's the stage-5 condition (controlled, honest stop).
        self._suppress_counts: dict[str, int] = {}

    # --- suppression bookkeeping ---

    def tick(self, iteration: int) -> None:
        """Expire any suppressions whose window has passed. Call once per loop
        iteration before checking is_suppressed()."""
        self._suppressed = {
            sig: exp for sig, exp in self._suppressed.items() if exp > iteration
        }

    def is_suppressed(self, signature: str) -> bool:
        return signature in self._suppressed

    def suppression_message(self, name: str) -> str:
        return (
            f"[{name} is temporarily unavailable: you have repeated this exact "
            f"call without making progress, and re-running it changes nothing. "
            f"Do something DIFFERENT — inspect the problem another way, change "
            f"your approach, or if you are stuck, stop and summarize honestly. "
            f"This restriction lifts once the repository state changes.]"
        )

    # --- the escalation decision ---

    def escalate(
        self,
        recommended_stage: int | None,
        *,
        offender_signature: str | None,
        iteration: int,
        suppress_window: int | None = None,
    ) -> RecoveryDirective:
        """Advance to `recommended_stage` (never downgrade mid-plateau) and return
        the directive for the current action. `offender_signature` is the exact
        (name,inputs) signature of the looping call, suppressed at stage >= 3.
        Progress (recommended_stage is None) resets to stage 0."""
        if not recommended_stage:
            self.stage = 0
            return RecoveryDirective(stage=0)
        # Escalate monotonically while stalled — never step back down until the
        # run actually makes progress (which clears it via the None branch above).
        self.stage = max(self.stage, int(recommended_stage))
        stage = self.stage

        if stage == 1:
            return RecoveryDirective(stage=1, note=(
                "[NOTE: you appear to be repeating an action without making "
                "progress. Before your next step, check whether it will actually "
                "move you closer to the goal.]"
            ))
        if stage == 2:
            # FIRST-PRINCIPLES REFLECTION (2026-08-30). The earlier wording asked for
            # goal / known / failed-strategy / new-strategy / avoid-repeating. Measured
            # on terminal-bench-2, this five-question form does better on exactly the
            # situation this stage fires in — a run that is stuck:
            #   89-task sweep, single attempt, no oracle: mean progress-milestone score
            #   1.00 (this form) vs 0.50 (the plain nudge), and it solved a task that had
            #   been a measured flatline. Two additions carry most of the difference:
            #   (a) "what am I ASSUMING / how do I know?" — stuck runs are usually stuck
            #       on an inherited false premise, not on a missing strategy;
            #   (b) "the SIMPLEST thing I can test" — it converts reflection into a cheap
            #       experiment instead of another full attempt.
            # It asks for CONCRETE success criteria first because externalising "what does
            # done mean" is the part that tracked the gain (criteria written in 7/8 of the
            # winning arm's runs vs 3/8 of a longer 10-step variant, which was followed
            # LESS — brevity matters here, so resist growing this list).
            # IMPORTANT — apply only when STUCK: the same prompt applied to every task
            # REGRESSED tasks the model would otherwise have solved (it overthinks working
            # code). Stage 2 is the right home precisely because it fires on a stall.
            return RecoveryDirective(stage=2, note=(
                "[STOP AND REFLECT: you have repeated actions without progress. "
                "Before doing anything else, answer these five questions briefly, "
                "in order: (1) What do I want? — the required outcome in one "
                "sentence, as concrete checkable criteria (exact files, formats, "
                "values), not prose. (2) What do I KNOW? — only facts you have "
                "VERIFIED by looking at the actual files/errors. (3) What am I "
                "ASSUMING? — for each, how do you know it? Check the ones you can; "
                "a stuck run is usually stuck on a false assumption. (4) What "
                "PREVENTS the outcome right now? — the single specific blocker. "
                "(5) What is the SIMPLEST thing I can test to find out if I am "
                "right? Then run that smallest test — prefer running something "
                "over reasoning about it.]"
            ))
        if stage == 3:
            if offender_signature and offender_signature not in self._suppressed:
                # A FRESH arming (not a refresh of a live window). A second fresh
                # arming of the same signature means the first suppression ran its
                # course — guidance included — and the model came straight back to
                # the same call with the same result. Stage 4 failed implicitly;
                # stop honestly rather than suppress-expire-repeat forever
                # (PRD K1.6).
                self._suppress_counts[offender_signature] = \
                    self._suppress_counts.get(offender_signature, 0) + 1
                if self._suppress_counts[offender_signature] >= 2:
                    self.stage = 5
                    return RecoveryDirective(stage=5, terminate=True, note=(
                        "[Recovery ceiling reached: this exact action kept "
                        "repeating even after being suppressed once — continuing "
                        "will not make progress. Stop and report honestly what "
                        "was accomplished and what remains; do NOT claim the "
                        "task is complete if it is not verified.]"
                    ))
                # A caller may widen the window (`suppress_window`) — an action
                # cycling with period N evades the default window when N >= it
                # (armed at k, retried at k+N, already expired).
                self._suppressed[offender_signature] = iteration + (
                    suppress_window or self.suppression_iterations)
            return RecoveryDirective(stage=3, suppress=bool(offender_signature), note=(
                "[This repeated action is now temporarily suppressed because it "
                "has not been making progress. Take a genuinely different step.]"
            ))
        if stage == 4:
            return RecoveryDirective(stage=4, note=(
                "[RESET: the current approach is not working and repeating it will "
                "not help. Abandon it. Re-derive the problem from what you have "
                "VERIFIED so far, and try a fundamentally different approach. Do "
                "NOT repeat the failed strategy.]"
            ))
        # stage 5
        return RecoveryDirective(stage=5, terminate=True, note=(
            "[Recovery ceiling reached: repeated attempts have not made progress. "
            "Stop now and report honestly what was accomplished, what remains, and "
            "why — do NOT claim the task is complete if it is not verified.]"
        ))

"""Holdout verification — confirm a claimed solve with a check the agent never saw.

Mitigation 1 for the reward hacking recorded in docs/modular_context_runtime_prd.md
Appendix A.4, where five consecutive runs on an unsatisfiable spec fabricated a passing
score. The escalation ended at stack introspection:

    caller = inspect.stack()[1].function
    if caller == "test_contradictory_requirement":
        return 3

which returns genuine ints and is correct for every caller except the single test it
names. No assertion written in the same language can defeat that, because the callee can
see who is asking. What it cannot do is special-case a test it has never seen.

Design:
  * The holdout CONFIRMS, it does not replace. Making it the primary signal would simply
    move the target; its value is that the agent optimised against a different check.
  * Fail CLOSED on disagreement, OPEN on infrastructure failure. A holdout that scores
    less than full is evidence the claim is false. A holdout that could not run at all is
    evidence of nothing, and must not silently reject honest work.
  * Advisory, not blocking (project rule): a rejected claim downgrades the round and says
    so loudly; it never kills the run.

Pure/stdlib, no TUI imports.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass

from drydock.ratchet import run_shell_bounded, score_output


@dataclass
class HoldoutResult:
    """Outcome of confirming a claimed solve."""
    ran: bool = False
    agreed: bool = False        # the holdout also reports a full pass
    inconclusive: bool = False  # could not run — NOT evidence against the claim
    passed: int = 0
    total: int = 0
    output: str = ""
    reason: str = ""

    def verdict(self) -> str:
        if not self.ran:
            return "not configured"
        if self.inconclusive:
            return f"inconclusive — {self.reason}"
        if self.agreed:
            return f"confirmed {self.passed}/{self.total}"
        return f"REJECTED — holdout scored {self.passed}/{self.total}, not a full pass"


def extract_holdout(arg: str) -> "tuple[str, str]":
    """Pull `--holdout <cmd>` out of a /ratchet argument string.

    Returns (remaining_arg, holdout_cmd). Implemented as a pre-pass so
    ratchet.parse_ratchet_args keeps its existing signature and callers are unaffected.
    """
    try:
        toks = shlex.split(arg or "")
    except ValueError:
        return arg, ""
    out: list = []
    holdout = ""
    i = 0
    while i < len(toks):
        if toks[i] == "--holdout" and i + 1 < len(toks):
            holdout = toks[i + 1]
            i += 2
            continue
        out.append(toks[i])
        i += 1
    return " ".join(shlex.quote(t) if " " in t else t for t in out), holdout


def confirm(holdout_cmd: str, cwd: str, *, fitness: str = "auto",
            timeout: int = 900) -> HoldoutResult:
    """Run the holdout and judge a claimed solve. Never raises."""
    if not holdout_cmd:
        return HoldoutResult(ran=False, reason="no holdout configured")
    try:
        out, rc, timed_out = run_shell_bounded(holdout_cmd, cwd, timeout)
    except OSError as e:
        return HoldoutResult(ran=True, inconclusive=True,
                             reason=f"holdout failed to start: {e}")
    if timed_out:
        return HoldoutResult(ran=True, inconclusive=True, output=out,
                             reason="holdout timed out")
    # Shell conventions for "the command itself did not run": 127 not found,
    # 126 not executable. These are infrastructure failures, and scoring them as 0/1
    # would let a typo'd holdout silently veto an honest solve.
    if rc in (126, 127):
        return HoldoutResult(ran=True, inconclusive=True, output=out,
                             reason=f"holdout command could not be executed (exit {rc})")
    passed, total = score_output(out, fitness, rc)
    if total <= 0:
        # nothing gradeable: the holdout told us nothing, so it must not veto.
        return HoldoutResult(ran=True, inconclusive=True, passed=passed, total=total,
                             output=out, reason="holdout produced no gradeable checks")
    agreed = passed >= total
    return HoldoutResult(ran=True, agreed=agreed, passed=passed, total=total, output=out,
                         reason="" if agreed else "holdout did not reproduce a full pass")


def corroboration_label(holdout_cmd: str, res: HoldoutResult) -> str:
    """What to record as `corroborated_by` when a claim survives the holdout. Naming the
    actual command matters — a later reader must be able to judge whether the
    corroboration was worth anything (context_runtime.corroborate)."""
    return f"holdout `{holdout_cmd}` scored {res.passed}/{res.total}"

"""Bridge between a Ratchet round and the Modular Context Runtime (PRD §11).

The ratchet already decides, every round, whether work was verified progress
(`pawl`/`solved`) or a dead end (`rollback`). Those are exactly the two events the
context runtime cares about:

  * pawl/solved → a ContextCheckpoint taken alongside the GitCheckpoint, tagged with
    the git ref and the verifier score that justified it. Ratchet stops being a git
    rollback mechanism and becomes cumulative selection over CODE *and* KNOWLEDGE.
  * rollback    → the attempt is tombstoned with the verifier's own evidence (score
    plus the specific failing checks), so the next round inherits "this was tried and
    why it failed" as ~a few hundred tokens instead of the whole dead transcript.

Pure/stdlib, no TUI imports (so it is unit-testable), and every public method swallows
its own errors: context bookkeeping must never break a ratchet run.
"""
from __future__ import annotations

import re
import time
import traceback

from drydock.context_runtime import (
    ContextCheckpoint,
    ContextModule,
    ContextStore,
    tombstone,
)

# pytest-style failure lines: "FAILED tests/test_x.py::test_y - reason" / "ERROR ..."
_FAILED = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.M)


def failed_checks(output: str, limit: int = 20) -> list:
    """The specific checks a verifier reported failing. This is what makes a tombstone
    *evidence-backed* rather than a model-authored hunch (PRD Appendix A.3)."""
    if not output:
        return []
    seen: list = []
    for m in _FAILED.finditer(output):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
        if len(seen) >= limit:
            break
    return seen


class ContextRatchet:
    """Records a ratchet run's rounds into a ContextStore. Construct once per run;
    call on_round() after each round's verify+record."""

    def __init__(self, cwd: str, run_id: str = "", goal: str = ""):
        self.run_id = run_id or f"ratchet-{int(time.time())}"
        self.store = ContextStore(root=cwd, name=self.run_id)
        self.checkpoint = ContextCheckpoint(self.store)
        self.goal = goal
        try:
            from drydock.context_inject import register
            register(cwd, self.store)          # makes this run's store injectable
        except Exception:  # noqa: BLE001
            pass
        if goal:
            try:
                # the objective is PINNED: it is never the thing we evict (§6)
                self.store.put(ContextModule(
                    context_id="ctx://task/objective", body=goal, type="task",
                    residency="pinned", scope="task", verified=True))
            except Exception:  # noqa: BLE001 — bookkeeping must never break a run
                pass

    def on_round(self, *, round_no: int, action: str, passed: int, total: int,
                 git_ref: str = "", verifier_output: str = "",
                 approach: str = "") -> dict:
        """Fold one ratchet round into the context store.

        `action` is RatchetState.record()'s return: 'solved' | 'pawl' | 'rollback'.
        Returns a small summary dict (never raises)."""
        out = {"action": action, "checkpoint": "", "tombstone": "", "claim": ""}
        try:
            fitness = (passed / total) if total else 0.0
            if action in ("pawl", "solved"):
                out["checkpoint"] = self.checkpoint.snapshot(
                    f"round {round_no} {passed}/{total}",
                    git_ref=git_ref, fitness=fitness)
                # Record the CLAIM, at the weak strength only. A passing verifier is
                # exactly what a reward-hacked patch manufactures (PRD Appendix A.4 —
                # observed: `class _AlwaysEq: __eq__ -> True` scored 10/10), so this is
                # verifier_passed, NOT verified, and therefore cannot climb past BRANCH
                # until something independent corroborates it.
                self.store.put(ContextModule(
                    context_id=f"ctx://result/r{round_no}",
                    body=(f"round {round_no}: verifier reported {passed}/{total} "
                          f"(fitness {fitness:.3f}) at {git_ref[:12] or 'unknown ref'}"),
                    type="decision", residency="working", scope="branch",
                    verifier_passed=True, verified=False,
                ))
                out["claim"] = f"ctx://result/r{round_no}"
            elif action == "rollback":
                attempt_id = f"ctx://attempt/r{round_no}"
                self.store.put(ContextModule(
                    context_id=attempt_id,
                    body=(approach or f"round {round_no} attempt"),
                    type="hypothesis", residency="working", scope="branch",
                ))
                failed = failed_checks(verifier_output)
                t = tombstone(
                    self.store, attempt_id,
                    approach=(approach or f"round {round_no} attempt")[:400],
                    result=f"scored {passed}/{total}; no improvement over the incumbent",
                    reason="verifier did not improve — round rolled back",
                    revisit_if="the failing checks change, or a later round makes this "
                               "approach viable",
                    evidence={"round": round_no, "passed": passed, "total": total,
                              "failed": failed},
                )
                out["tombstone"] = t.context_id if t else ""
        except Exception as e:  # noqa: BLE001 — never break the ratchet…
            self._log_error(f"on_round(round={round_no}, action={action})", e)
        return out

    def _log_error(self, where: str, exc: Exception) -> None:
        """…but never swallow SILENTLY either. A recorder that fails invisibly is
        indistinguishable from one that works, which has already cost this project two
        debugging cycles. Errors land next to the data they should have produced."""
        try:
            with (self.store.dir / "errors.log").open("a", encoding="utf-8") as f:
                f.write(f"{time.time():.0f} {where}: {type(exc).__name__}: {exc}\n")
                f.write(traceback.format_exc() + "\n")
        except OSError:
            pass

    def summary(self) -> dict:
        """What the run accumulated — for a status line or /context."""
        try:
            mods = self.store.all()
            return {
                "run_id": self.run_id,
                "modules": len(mods),
                "tombstones": len([m for m in mods if m.residency == "tombstoned"]),
                "checkpoints": len(self.checkpoint._all()),  # noqa: SLF001 — same package
                "tokens_stored": sum(m.token_size for m in mods),
            }
        except Exception:  # noqa: BLE001
            return {"run_id": self.run_id}

"""Bottleneck register — after a system is decomposed, rank its KNOWN components by
how much the objective actually moves if you improve each one, and attack the one
limiting factor instead of optimising everything evenly.

WHY THIS IS A SEPARATE STEP FROM THE LEDGER
-------------------------------------------
`groundtruth.py` ranks UNKNOWNS by decision impact — it answers "what don't I know
that could change the plan?" That is an epistemic question. The bottleneck is a
different question, and conflating the two loses it: the limiting factor is usually
something you already KNOW with no uncertainty at all. You can be perfectly certain
which stage is slowest and still pour effort into a faster one.

This project is its own evidence. Two of its largest course-corrections were bottleneck
findings, not discoveries:
  · "throughput is the master lever" — the search was being made cleverer for weeks while
    the objective barely moved, because the limiting factor was how many traces the box
    could produce per hour, not how good each one was.
  · "the real lever is training solves back into the model (write-back), not fancier
    search" — the same shape a second time: effort concentrated on a component with lots
    of headroom but little SHARE of the objective, while the component with the share sat
    untouched.
Neither of those was an unknown. `next_test()` would not have surfaced either, because
there was nothing to learn — only a limiting factor to name and attack.

THE ONE NUMBER IT COMPUTES
--------------------------
For a component that controls a fraction `share` of the objective's gap and still has a
fraction `headroom` left to improve, the realizable gain from perfecting it is
`share * headroom`. The bottleneck is the component with the largest realizable gain —
NOT the one that is easiest to improve (high headroom, low share: the trap this whole
module exists to name), and NOT the biggest cost that happens to be a fundamental
constraint you cannot move (high share, zero headroom: a wall, not a lever).

The Amdahl ceiling `share` is reported alongside: it is the most that component could
EVER buy you, even with an infinitely good improvement. A lever with a 5% ceiling is
capped at 5% no matter how brilliant the optimisation — that ceiling is the sentence
that ends most premature-optimisation arguments.

WHY THIS IS NOT A PROMPT
Same reason as the other two modules: a reasoning checklist telling the model to "find
the bottleneck first" was measured on terminal-bench-2 and lost to a plain retry. This
gives the model somewhere to put the decomposition and computes the one comparison it is
bad at doing in its head — the share×headroom product across a handful of components.

Advisory by contract: nothing here raises on bad input, blocks a call, or edits a plan.
It records, ranks, and renders. See also `groundtruth.py` and `predictions.py`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

# A component is a "wall" (fundamental constraint, not a lever) once its headroom is
# this low: no realistic optimisation moves it, so naming it stops you re-attacking it.
_WALL_HEADROOM = 0.05


def _clamp01(x: object, default: float = 0.0) -> float:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


@dataclass
class Component:
    """One piece of the decomposed system, and its two numbers that matter."""
    id: str
    name: str = ""
    # Fraction of the objective's gap this component controls (its Amdahl share). If the
    # objective is end-to-end latency and this stage is 70% of it, share = 0.7. This is a
    # SENSITIVITY, not the effort currently spent on it — the whole point is that the two
    # come apart.
    share: float = 0.0
    # Fraction of THIS component still improvable. 0 = a fundamental constraint (physics,
    # a hard resource limit, already optimal) you cannot move; 1 = could be eliminated
    # entirely. This is where step 4 (fundamental constraints) enters the arithmetic.
    headroom: float = 1.0
    note: str = ""

    def gain(self) -> float:
        """Realizable objective gain from exploiting this component's headroom."""
        return self.share * self.headroom

    def ceiling(self) -> float:
        """Amdahl ceiling: the most this component could EVER buy, headroom aside."""
        return self.share

    def is_wall(self) -> bool:
        return self.share > 0 and self.headroom <= _WALL_HEADROOM

    def summary(self) -> str:
        mark = "▮" if self.is_wall() else "▲"
        return (f"{mark} {self.id} {self.name[:56]} "
                f"[gain {self.gain():.0%} · ceiling {self.ceiling():.0%} · "
                f"headroom {self.headroom:.0%}]")


class Bottlenecks:
    """The decomposed components of one problem, persisted next to the run."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.items: list[Component] = []
        if self.path and self.path.exists():
            self.load()

    # ── recording ────────────────────────────────────────────────────────────
    def add(self, name: str, *, share: float = 0.0, headroom: float = 1.0,
            note: str = "") -> Component:
        """Record a decomposed component. Out-of-range numbers are clamped to [0,1]
        rather than raising — a register that throws would take down the turn it is
        meant to help."""
        c = Component(id=f"c{len(self.items) + 1}",
                      name=(name or "").strip(),
                      share=_clamp01(share, 0.0),
                      headroom=_clamp01(headroom, 1.0),
                      note=(note or "").strip())
        self.items.append(c)
        self._save()
        return c

    def update(self, cid: str, *, share: float | None = None,
               headroom: float | None = None, note: str | None = None) -> Component | None:
        """Revise a component's numbers as measurement replaces estimate. Attacking the
        bottleneck moves its headroom toward 0, at which point the ranking shifts to the
        next limiting factor on its own."""
        for c in self.items:
            if c.id == cid:
                if share is not None:
                    c.share = _clamp01(share, c.share)
                if headroom is not None:
                    c.headroom = _clamp01(headroom, c.headroom)
                if note is not None:
                    c.note = (note or "").strip()
                self._save()
                return c
        return None

    # ── the ranking that is the point of the module ──────────────────────────
    def _levers(self) -> list[Component]:
        """Components that are actually worth attacking — positive gain and not a wall.
        A wall's sliver of headroom is not a lever, so it is excluded here even though its
        gain is technically > 0."""
        return [c for c in self.items if c.gain() > 0 and not c.is_wall()]

    def bottleneck(self) -> Component | None:
        """The one limiting factor to attack first: the largest realizable gain
        (share × headroom). Ties break toward the larger share — the higher ceiling is
        the safer bet once the immediate gain is equal."""
        movable = self._levers()
        if not movable:
            return None
        return sorted(movable, key=lambda c: (-c.gain(), -c.share, c.id))[0]

    def ranked(self) -> list[Component]:
        return sorted(self.items, key=lambda c: (-c.gain(), -c.share, c.id))

    def walls(self) -> list[Component]:
        """High-share components with (almost) no headroom — fundamental constraints.
        Naming them is what stops the loop re-attacking a wall as if it were a lever."""
        return [c for c in self.items if c.is_wall()]

    def misplaced_effort(self) -> Component | None:
        """The trap this module exists to catch: the component with the MOST headroom is
        not the bottleneck — improving it feels productive but is capped by its small
        share. Returns that decoy when it differs from the real bottleneck, else None."""
        movable = self._levers()
        if len(movable) < 2:
            return None
        by_headroom = max(movable, key=lambda c: (c.headroom, -c.share))
        bn = self.bottleneck()
        return by_headroom if bn is not None and by_headroom.id != bn.id else None

    # ── rendering ────────────────────────────────────────────────────────────
    def render(self) -> str:
        if not self.items:
            return "(no components decomposed yet)"
        lines = [c.summary() for c in self.ranked()]
        bn = self.bottleneck()
        if bn is not None:
            lines.append(f"→ attack first: {bn.id} ({bn.name[:50]}) — "
                         f"realizable gain {bn.gain():.0%}, ceiling {bn.ceiling():.0%}")
        else:
            lines.append("→ no movable component: every lever is a wall "
                         "(headroom ~0). The objective may need a different decomposition.")
        decoy = self.misplaced_effort()
        if decoy is not None:
            lines.append(f"⚠ {decoy.id} has the most headroom but only a "
                         f"{decoy.ceiling():.0%} ceiling — optimising it is capped there.")
        total_share = sum(c.share for c in self.items)
        if total_share > 1.05:
            lines.append(f"⚠ shares sum to {total_share:.0%} (>100%): the decomposition "
                         "overlaps — components are not independent.")
        return "\n".join(lines)

    # ── persistence ──────────────────────────────────────────────────────────
    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps([asdict(c) for c in self.items], indent=2), encoding="utf-8")
        except OSError:
            pass          # advisory: never fail a run over bookkeeping

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path else []
            self.items = [Component(**{k: v for k, v in d.items()
                                       if k in Component.__dataclass_fields__}) for d in raw]
        except (OSError, json.JSONDecodeError, TypeError):
            self.items = []

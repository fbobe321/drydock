"""Adaptive Swarm Runtime (ASR) — the search-decision core.

This is the resource-agnostic brain of the adaptive swarm (PRD "Resource-Aware Adaptive
Swarms"): it turns a compute/time BUDGET into decisions about *where the next unit of inference
should go* and *when to stop*. It deliberately holds no model, GPU, or scheduler state — those
live in capacity.py (concurrency), swarm.py (blackboard/candidates/worktrees) and the mission
store (durable state). Keeping the decisions pure makes them unit-testable and reusable.

Three pieces, in dependency order:
  * Budget      — a finite termination envelope parsed from a user string (§15).
  * Convergence — should the swarm keep going? Stops on success / budget / no-improvement /
                  when marginal value Δquality/Δcompute falls below a floor (§23).
  * AdaptiveAllocator — split the next compute pool across live branches by expected value,
                  pruning the weak ones (§17). Bad ideas must not get the same compute as good.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Budget (§15) ────────────────────────────────────────────────────────────
_DUR = re.compile(r"^\s*([\d.]+)\s*([hms])\s*$", re.I)
_TOK = re.compile(r"^\s*([\d.]+)\s*([kmg]?)\s*[-_]?tokens?\s*$", re.I)
_MULT = {"": 1, "k": 1_000, "m": 1_000_000, "g": 1_000_000_000}


@dataclass
class Budget:
    """A finite stopping envelope (§10/§15). Every field is optional, but a Budget with no
    finite limit is a bug — `is_finite` guards against "run forever"."""
    wall_secs: float | None = None
    tokens: int | None = None
    generations: int | None = None

    @property
    def is_finite(self) -> bool:
        return any(v is not None for v in (self.wall_secs, self.tokens, self.generations))

    @classmethod
    def parse(cls, spec: str) -> "Budget":
        """Parse one user token: '2h' / '30m' / '90s' (wall) or '100M-tokens' / '50k-tokens'
        (compute). Unknown strings raise ValueError so a typo never silently means 'unbounded'."""
        s = (spec or "").strip()
        md = _DUR.match(s)
        if md:
            n, unit = float(md.group(1)), md.group(2).lower()
            return cls(wall_secs=n * {"h": 3600, "m": 60, "s": 1}[unit])
        mt = _TOK.match(s)
        if mt:
            return cls(tokens=int(float(mt.group(1)) * _MULT[mt.group(2).lower()]))
        raise ValueError(f"unparseable budget: {spec!r} (try '2h', '30m', '100M-tokens')")


# ── Convergence (§23) ─────────────────────────────────────────────────────────
@dataclass
class GenerationRecord:
    gen: int
    best_score: float          # best branch quality so far (higher = better)
    cumulative_tokens: int     # total inference tokens spent through this generation
    elapsed_s: float           # wall-clock since start


@dataclass
class Convergence:
    """Decides whether the swarm should run another generation (§23). "More agents are not
    automatically better": we stop the moment any finite condition trips, INCLUDING the marginal
    one — when Δquality per unit compute drops below `min_marginal`, extra inference is waste."""
    budget: Budget
    success_score: float = 100.0     # stop when best branch reaches this (e.g. all tests pass)
    no_improve_gens: int = 3         # stop after this many generations with no best-score gain
    min_marginal: float = 0.0        # floor on Δscore per 1k tokens; <=0 disables the check
    eps: float = 1e-9
    history: list[GenerationRecord] = field(default_factory=list)

    def record(self, best_score: float, cumulative_tokens: int, elapsed_s: float) -> None:
        self.history.append(GenerationRecord(len(self.history) + 1, best_score,
                                             cumulative_tokens, elapsed_s))

    def _gens_since_improvement(self) -> int:
        """Generations since the best score was FIRST reached (a plateau at the best counts)."""
        if not self.history:
            return 0
        best = max(r.best_score for r in self.history)
        first_best = next(i for i, r in enumerate(self.history) if r.best_score >= best - self.eps)
        return (len(self.history) - 1) - first_best

    def marginal_value(self) -> float | None:
        """Δscore per 1,000 tokens over the last generation, or None with <2 records."""
        if len(self.history) < 2:
            return None
        a, b = self.history[-2], self.history[-1]
        dtok = b.cumulative_tokens - a.cumulative_tokens
        if dtok <= 0:
            return None
        return (b.best_score - a.best_score) / (dtok / 1000.0)

    def should_continue(self) -> tuple[bool, str]:
        """(keep_going?, reason). Checked in priority order; the reason names the stop trigger."""
        last = self.history[-1] if self.history else None
        if last is not None and last.best_score >= self.success_score - self.eps:
            return False, "success score reached"
        b = self.budget
        if b.wall_secs is not None and last is not None and last.elapsed_s >= b.wall_secs:
            return False, "wall-clock budget exhausted"
        if b.tokens is not None and last is not None and last.cumulative_tokens >= b.tokens:
            return False, "token budget exhausted"
        if b.generations is not None and len(self.history) >= b.generations:
            return False, "generation budget reached"
        if self.no_improve_gens > 0 and self._gens_since_improvement() >= self.no_improve_gens:
            return False, f"no improvement in {self.no_improve_gens} generations"
        mv = self.marginal_value()
        if self.min_marginal > 0 and mv is not None and mv < self.min_marginal:
            return False, f"marginal value {mv:.3f} < floor {self.min_marginal:.3f} (Δquality/Δcompute)"
        return True, "continue"


# ── Adaptive compute allocation (§17) ──────────────────────────────────────────
@dataclass
class Allocation:
    grants: dict[str, int]     # branch id -> tokens granted this round
    terminated: list[str]      # branch ids pruned (0 tokens, killed)


def allocate(branches: list[tuple[str, float]], pool_tokens: int, *,
             prune_below: float = 0.0, keep_top: int | None = None,
             bias: float = 1.5, min_grant: int = 0) -> Allocation:
    """Split `pool_tokens` across branches by expected value (§17).

    branches: (id, score) with higher score = more promising. A branch is TERMINATED (gets 0)
    if its score is below `prune_below` or it falls outside the top `keep_top`. Survivors share
    the pool in proportion to score**bias, so leaders get disproportionately more (bias>1) — the
    system must not spend equally on good and bad ideas. `min_grant` guarantees a floor so a
    kept-but-trailing branch still gets a probe. Deterministic; ties broken by input order."""
    if pool_tokens <= 0 or not branches:
        return Allocation({}, [b for b, _ in branches])
    ranked = sorted(branches, key=lambda x: x[1], reverse=True)
    survivors = [(b, s) for b, s in ranked if s >= prune_below]
    if keep_top is not None:
        survivors = survivors[:max(0, keep_top)]
    kept_ids = {b for b, _ in survivors}
    terminated = [b for b, _ in branches if b not in kept_ids]
    if not survivors:
        return Allocation({}, terminated)
    weights = [(b, max(s, 0.0) ** bias) for b, s in survivors]
    total_w = sum(w for _, w in weights) or float(len(weights))
    grants: dict[str, int] = {}
    for b, w in weights:
        share = (w / total_w) if total_w else 1.0 / len(weights)
        grants[b] = max(min_grant, int(round(pool_tokens * share)))
    # correct rounding drift so grants sum to the pool (adjust the top branch)
    drift = pool_tokens - sum(grants.values())
    if drift and weights:
        top = weights[0][0]
        grants[top] = max(min_grant, grants[top] + drift)
    return Allocation(grants, terminated)

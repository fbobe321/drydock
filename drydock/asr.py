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


# ── Planner: budget + intensity + hardware → population plan (§15/§16, §5/§9) ──
# Diverse roles for solution-space coverage (§9/§10). Correlated agents waste compute.
ROLES = [
    "first-principles solver", "conventional solver", "skeptic", "debugger",
    "security reviewer", "researcher", "minimalist", "adversarial critic",
    "performance specialist", "alternative-architecture agent",
]

# Per-intensity search shape (§16): initial population, critic count, generation cap, and how
# many branches survive each prune. Population is a SEARCH choice — independent of hardware.
INTENSITY = {
    "light":    {"population": 3,  "critics": 1, "generations": 2,  "keep": 2},
    "standard": {"population": 8,  "critics": 2, "generations": 4,  "keep": 4},
    "deep":     {"population": 16, "critics": 3, "generations": 6,  "keep": 6},
    "extreme":  {"population": 24, "critics": 5, "generations": 10, "keep": 8},
}


@dataclass
class SwarmPlan:
    intensity: str
    initial_population: int      # logical agents in the explore stage (§9)
    max_concurrency: int         # simultaneous inference — HARDWARE bound (§5), != population
    critics: int                 # judge/critic agents (§11)
    generations: int             # generation cap (a convergence bound, §23)
    keep: int                    # survivors promoted past each prune (§12)
    roles: list[str]             # diversified assignments (§10)
    estimated_logical: int       # rough total agents incl. spawned descendants (§13) — info only

    @property
    def queued_at_start(self) -> int:
        """Logical agents that must wait because concurrency < population (§18)."""
        return max(0, self.initial_population - self.max_concurrency)


def choose_intensity(budget: Budget) -> str:
    """Map a budget to a search intensity for `--swarm auto` (§16). Bigger budget → deeper
    search. Falls back to 'standard' when the budget carries no size signal."""
    if budget.wall_secs is not None:
        s = budget.wall_secs
        return "light" if s < 900 else "standard" if s < 3600 else "deep" if s < 14400 else "extreme"
    if budget.tokens is not None:
        t = budget.tokens
        return ("light" if t < 1_000_000 else "standard" if t < 10_000_000
                else "deep" if t < 50_000_000 else "extreme")
    if budget.generations is not None:
        g = budget.generations
        return "light" if g <= 2 else "standard" if g <= 4 else "deep" if g <= 6 else "extreme"
    return "standard"


def plan(budget: Budget, concurrency: int, *, intensity: str = "auto") -> SwarmPlan:
    """Turn a budget + measured hardware concurrency into a population plan (§15/§16).

    KEY: logical population comes from the intensity (a search decision); `concurrency` only
    caps how many run at once (§5). On a 1-GPU box a 16-agent 'deep' plan still has 16 logical
    agents — the rest queue (§18); the penalty is wall-clock, not lost search capability. This
    is the opposite of capacity.swarm_size(), which caps logical count AT concurrency."""
    intensity = choose_intensity(budget) if intensity == "auto" else intensity
    if intensity not in INTENSITY:
        raise ValueError(f"unknown intensity {intensity!r} (light|standard|deep|extreme|auto)")
    p = INTENSITY[intensity]
    pop = p["population"]
    roles = [ROLES[i % len(ROLES)] for i in range(pop)]
    # estimate total logical agents: initial + ~one descendant per survivor per later generation
    estimated = pop + p["keep"] * max(0, p["generations"] - 1)
    return SwarmPlan(intensity=intensity, initial_population=pop,
                     max_concurrency=max(1, concurrency), critics=p["critics"],
                     generations=p["generations"], keep=p["keep"], roles=roles,
                     estimated_logical=estimated)

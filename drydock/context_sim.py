"""Deterministic synthetic-ratchet fixture — "the first test to actually run"
(mcr_validation_prd.md §33), with no LLM in the loop.

MCR §2's philosophy is: do NOT begin with a large coding benchmark; build a deterministic
environment where correct behaviour is known in advance, and only then interpret noisy
real-benchmark results. This module is that environment for the MCR → ABC → Laya integration.
A scripted "solver" walks a fixed trajectory (incremental progress, a plateau, a regressing
wrong branch, a dead-end strategy family, and finally the correct branch), and the REAL control
components decide what happens:

  * ratchet.RatchetState        — pawls improvements, rolls back regressions (the real pawl);
  * adaptive_budget.RatchetBudgetAdvisor — escalates/de-escalates the resource envelope;
  * decision.StrategyLedger     — collapses near-duplicate approaches and flags a dead end.

If the integration is wired correctly the report is predictable: the plateau escalates the
budget, the wrong branch rolls back, the repeated family invalidates, and the correct branch
solves. That is what the test asserts.

HONEST SCOPE. The token/cache figures are a TRANSPARENT PLACEHOLDER model (documented inline),
NOT a measurement of real KV/prefix-cache behaviour — MCR §0 already showed that only the live
backend can answer the efficiency question, and this project has burned runs mistaking a model
for a measurement. The fields are labelled `modeled_*` for exactly that reason. What this
fixture proves is the CONTROL LOGIC, deterministically; the numbers are illustrative.

All logic original to Drydock. Never raises for control reasons.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .adaptive_budget import RatchetBudgetAdvisor
from .decision import HeuristicProvider, StrategyLedger
from .ratchet import RatchetState

MODES = ("append", "modular", "cache_aware")


@dataclass
class Module:
    name: str
    tokens: int = 4000
    permanent: bool = False       # permanent facts are never evicted (§33: 5 permanent facts)


@dataclass
class Round:
    """One scripted attempt. `approach` is the free-text strategy (folded into a family by the
    ledger); (passed, total) is the verifier outcome the ratchet folds in; `load`/`evict` name
    the module the policy touches this round (forced faults/evictions per §33)."""
    approach: str
    passed: int
    total: int
    load: str = ""
    evict: str = ""


@dataclass
class Scenario:
    modules: list[Module]
    rounds: list[Round]
    goal: str = "synthetic ratchet"
    base_tokens: int = 1800       # pinned system prompt (unpageable), ~MCR §0's measured 1.75K


@dataclass
class SimReport:
    mode: str
    solved: bool = False
    final_passed: int = 0
    final_total: int = 0
    rollbacks: int = 0
    pawls: int = 0
    escalations: int = 0
    deescalations: int = 0
    page_faults: int = 0          # module loads (§33: ≥2 forced page faults)
    evictions: int = 0            # module unloads (§33: ≥3 forced evictions)
    successful_restores: int = 0  # rollbacks that also restored context (Gate 4)
    dead_end_families: int = 0    # strategy families invalidated (§33: dead-end repetitions)
    modeled_peak_resident_tokens: int = 0
    modeled_total_prompt_tokens: int = 0
    modeled_uncached_prompt_tokens: int = 0
    wall_ms: float = 0.0
    reasoning_trajectory: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in self.__dict__.items()}


def default_scenario() -> Scenario:
    """The §33 scenario: 20 modules (5 permanent facts + others), incremental tests, a plateau,
    4 wrong branches, 1 correct branch, forced evictions/faults and a forced rollback."""
    mods = [Module(f"fact_{i}", 1500, permanent=True) for i in range(5)]
    mods += [Module(f"mod_{i}", 4000) for i in range(15)]
    rounds = [
        Round("read repository map", 5, 20, load="mod_0"),          # progress (pawl)
        Round("fix failing import", 8, 20, load="mod_1"),           # progress (pawl)
        Round("expand the lock scope", 8, 20),                       # plateau 1 (dead-end family)
        Round("increase mutex scope", 8, 20),                        # plateau 2 -> ABC escalates
        Round("lock a larger section", 8, 20),                       # plateau 3 -> family invalidates
        Round("try the wrong refactor", 3, 20, load="mod_5"),       # wrong branch: rollback + restore
        Round("load the database schema", 12, 20, load="mod_3", evict="mod_0"),  # progress + evict
        Round("wire the new query path", 16, 20, load="mod_4", evict="mod_1"),   # progress + evict
        Round("handle the edge case", 20, 20, evict="mod_3"),       # SOLVED (correct branch)
    ]
    return Scenario(modules=mods, rounds=rounds, goal="fix concurrency-related test failures")


def run(scenario: Scenario, mode: str, *, decider=None) -> SimReport:
    """Drive the scenario through the real control components under one context policy.

    `mode`:
      * append      — every module ever loaded stays resident (context only grows);
      * modular     — evicted modules leave residency (context can shrink);
      * cache_aware — modular residency AND a pinned stable prefix (evictions don't rewrite it,
                      so modeled uncached prefill stays low — the intended MCR §0 remedy).
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    t0 = time.monotonic()
    rep = SimReport(mode=mode)
    by_name = {m.name: m for m in scenario.modules}

    st = RatchetState(goal=scenario.goal, max_rounds=len(scenario.rounds))
    advisor = RatchetBudgetAdvisor(effort="low", decider=decider or HeuristicProvider(),
                                   objective=scenario.goal)
    ledger = StrategyLedger(invalidate_after=3)

    active: set[str] = {m.name for m in scenario.modules if m.permanent}
    ever_loaded: set[str] = set(active)
    prev_best = st.best_passed

    for rnd in scenario.rounds:
        # --- context policy: apply the scripted load/evict for this round ---
        if rnd.load and rnd.load in by_name:
            if rnd.load not in active:
                rep.page_faults += 1
            active.add(rnd.load)
            ever_loaded.add(rnd.load)
        if rnd.evict and mode != "append" and rnd.evict in active and not by_name[rnd.evict].permanent:
            active.discard(rnd.evict)
            rep.evictions += 1

        # --- fitness gain vs current best drives the strategy ledger (§15) ---
        gain = rnd.passed - max(prev_best, 0)
        ledger.record(rnd.approach, fitness_gain=max(0, gain))

        # --- the real pawl: pawl / rollback / solved ---
        action = st.record(rnd.passed, rnd.total, snapshot_ref=f"r{st.round}")
        if action == "pawl" or action == "solved":
            rep.pawls += 1 if action == "pawl" else 0
        elif action == "rollback":
            rep.rollbacks += 1
            # Gate 4: a rollback restores context with code — model it by dropping any module
            # this bad round pulled in that isn't otherwise justified.
            if rnd.load and rnd.load in active and not by_name[rnd.load].permanent:
                active.discard(rnd.load)
                rep.successful_restores += 1

        # --- ABC observes the honest score; note escalations/de-escalations ---
        note = advisor.observe_round(st.best_passed, rnd.total)
        rep.reasoning_trajectory.append(advisor.controller.envelope.reasoning.level)
        if note and "plateau" in note:
            rep.escalations += 1
        elif note and "releasing" in note:
            rep.deescalations += 1

        prev_best = st.best_passed

        # --- modeled token accounting (PLACEHOLDER, see module docstring) ---
        resident = scenario.base_tokens + sum(
            by_name[n].tokens for n in (ever_loaded if mode == "append" else active))
        rep.modeled_peak_resident_tokens = max(rep.modeled_peak_resident_tokens, resident)
        rep.modeled_total_prompt_tokens += resident
        # append reuses its stable growing prefix (low uncached rate); plain modular rewrites the
        # earlier region on eviction (higher uncached rate — the MCR §0 penalty); cache_aware
        # pins the prefix so eviction is cheap again.
        if mode == "append":
            rep.modeled_uncached_prompt_tokens += scenario.base_tokens // 4
        elif mode == "modular":
            rep.modeled_uncached_prompt_tokens += resident // 3
        else:  # cache_aware
            rep.modeled_uncached_prompt_tokens += scenario.base_tokens // 4

    rep.solved = st.best_total > 0 and st.best_passed >= st.best_total
    rep.final_passed, rep.final_total = st.best_passed, st.best_total
    rep.dead_end_families = len(ledger.lessons())
    rep.wall_ms = (time.monotonic() - t0) * 1000
    return rep


def run_all(scenario: Scenario | None = None, *, decider=None) -> dict[str, SimReport]:
    """Run the scenario under all three policies and return {mode: report} — the §33 comparison
    table, deterministically and without a model."""
    sc = scenario or default_scenario()
    return {mode: run(sc, mode, decider=decider) for mode in MODES}

"""Tests for the §33 deterministic synthetic-ratchet fixture (mcr_validation_prd.md).

These assert the CONTROL LOGIC of the MCR -> ABC -> Laya integration reacts correctly to a
scripted trajectory — the real point of the fixture. The modeled token figures are a
placeholder (see context_sim docstring), so tests assert their *shape* (modular <= append
residency), never an absolute number.
"""
from __future__ import annotations

import json

from drydock.context_sim import MODES, Scenario, default_scenario, run, run_all


def test_correct_branch_solves():
    rep = run(default_scenario(), "modular")
    assert rep.solved
    assert (rep.final_passed, rep.final_total) == (20, 20)


def test_wrong_branch_rolls_back():
    # the scripted round 6 regresses (3/20) and must be rolled back, not locked in.
    rep = run(default_scenario(), "modular")
    assert rep.rollbacks >= 1


def test_rollback_restores_context_gate4():
    # a rollback should also drop the module the bad round pulled in (Gate 4: context rolls
    # back with code).
    rep = run(default_scenario(), "modular")
    assert rep.successful_restores >= 1


def test_plateau_escalates_budget():
    # the three flat rounds (8/20) are a sustained plateau -> ABC must escalate at least once.
    rep = run(default_scenario(), "modular")
    assert rep.escalations >= 1
    # reasoning should have climbed above the initial "low" somewhere in the run.
    assert any(lvl != "low" for lvl in rep.reasoning_trajectory)


def test_dead_end_family_invalidated():
    # "expand/increase/lock ... scope/section" are one family tried 3x with no gain -> invalidate.
    rep = run(default_scenario(), "modular")
    assert rep.dead_end_families >= 1


def test_forced_faults_and_evictions_recorded():
    rep = run(default_scenario(), "modular")
    assert rep.page_faults >= 2       # §33: ≥2 forced page faults
    assert rep.evictions >= 3         # §33: ≥3 forced evictions


def test_append_never_evicts():
    rep = run(default_scenario(), "append")
    assert rep.evictions == 0
    assert rep.solved                 # policy doesn't change correctness, only residency


def test_modular_resident_not_above_append():
    reps = run_all()
    assert reps["modular"].modeled_peak_resident_tokens <= reps["append"].modeled_peak_resident_tokens


def test_cache_aware_uncached_not_above_modular():
    reps = run_all()
    # the whole point of cache-aware (MCR §0): pinning the prefix keeps modeled uncached prefill
    # at or below plain modular's.
    assert (reps["cache_aware"].modeled_uncached_prompt_tokens
            <= reps["modular"].modeled_uncached_prompt_tokens)


def test_all_modes_solve_and_report():
    reps = run_all()
    assert set(reps) == set(MODES)
    for mode, rep in reps.items():
        assert rep.solved, mode
        # report must be JSON-serialisable (it becomes a CI artifact / trace).
        json.dumps(rep.to_dict())


def test_deterministic():
    # same scenario + policy -> identical report (minus wall time).
    a = run(default_scenario(), "modular").to_dict()
    b = run(default_scenario(), "modular").to_dict()
    a.pop("wall_ms"); b.pop("wall_ms")
    assert a == b


def test_empty_scenario_is_safe():
    rep = run(Scenario(modules=[], rounds=[]), "modular")
    assert not rep.solved and rep.escalations == 0

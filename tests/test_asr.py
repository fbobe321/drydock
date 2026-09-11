"""Tests for the Adaptive Swarm Runtime search core (drydock/asr.py) — pure, no model/GPU."""
from __future__ import annotations

import pytest

from drydock import asr


# ── Budget (§15) ────────────────────────────────────────────────────────────
def test_budget_parse_time_and_tokens():
    assert asr.Budget.parse("2h").wall_secs == 7200
    assert asr.Budget.parse("30m").wall_secs == 1800
    assert asr.Budget.parse("90s").wall_secs == 90
    assert asr.Budget.parse("100M-tokens").tokens == 100_000_000
    assert asr.Budget.parse("50k-tokens").tokens == 50_000
    assert asr.Budget.parse("2h").is_finite


def test_budget_rejects_garbage_never_silently_unbounded():
    with pytest.raises(ValueError):
        asr.Budget.parse("whenever")
    assert asr.Budget().is_finite is False        # empty budget is explicitly not finite


# ── Convergence (§23) ─────────────────────────────────────────────────────────
def _conv(**kw):
    return asr.Convergence(budget=asr.Budget(generations=100), **kw)


def test_convergence_stops_on_success():
    c = _conv(success_score=100.0)
    c.record(best_score=100.0, cumulative_tokens=5000, elapsed_s=60)
    go, why = c.should_continue()
    assert not go and "success" in why


def test_convergence_stops_on_budget():
    c = asr.Convergence(budget=asr.Budget(tokens=10_000))
    c.record(best_score=40.0, cumulative_tokens=12_000, elapsed_s=30)
    go, why = c.should_continue()
    assert not go and "token budget" in why


def test_convergence_stops_on_no_improvement():
    c = _conv(no_improve_gens=2, success_score=100.0)
    c.record(50.0, 1000, 10)
    c.record(50.0, 2000, 20)     # no gain
    c.record(50.0, 3000, 30)     # still no gain -> 2 stagnant gens
    go, why = c.should_continue()
    assert not go and "no improvement" in why


def test_convergence_stops_on_low_marginal_value():
    # big jump, then a tiny gain for a lot of tokens -> marginal value below floor
    c = _conv(min_marginal=1.0, no_improve_gens=0, success_score=100.0)
    c.record(40.0, 1000, 10)
    c.record(80.0, 3000, 20)                 # +40 over 2k tok = 20/1k -> continues
    assert c.should_continue()[0] is True
    c.record(80.5, 13000, 40)                # +0.5 over 10k tok = 0.05/1k -> below 1.0 floor
    go, why = c.should_continue()
    assert not go and "marginal value" in why


def test_convergence_continues_while_improving():
    c = _conv(no_improve_gens=3, min_marginal=0.5, success_score=100.0)
    c.record(40.0, 1000, 10)
    c.record(60.0, 2000, 20)                 # +20/1k, improving, under budget
    assert c.should_continue() == (True, "continue")


def test_marginal_value_needs_two_records():
    c = _conv()
    assert c.marginal_value() is None
    c.record(10.0, 500, 5)
    assert c.marginal_value() is None


# ── Adaptive allocation (§17) ──────────────────────────────────────────────────
def test_allocate_favors_leaders_and_prunes_weak():
    branches = [("A", 0.91), ("B", 0.86), ("C", 0.43), ("D", 0.21)]
    alloc = asr.allocate(branches, 8000, prune_below=0.3)
    assert "D" in alloc.terminated and alloc.grants.get("D", 0) == 0   # weakest pruned
    assert set(alloc.grants) == {"A", "B", "C"}
    assert sum(alloc.grants.values()) == 8000                          # whole pool spent
    assert alloc.grants["A"] > alloc.grants["B"] > alloc.grants["C"]   # EV ordering
    assert alloc.grants["C"] < alloc.grants["A"] / 2                   # bias toward the leader


def test_allocate_keep_top_terminates_the_rest():
    branches = [("A", 0.9), ("B", 0.8), ("C", 0.7), ("D", 0.6)]
    alloc = asr.allocate(branches, 6000, keep_top=2)
    assert set(alloc.grants) == {"A", "B"}
    assert sorted(alloc.terminated) == ["C", "D"]
    assert sum(alloc.grants.values()) == 6000


def test_allocate_empty_pool_terminates_all():
    alloc = asr.allocate([("A", 0.9), ("B", 0.5)], 0)
    assert alloc.grants == {} and sorted(alloc.terminated) == ["A", "B"]


def test_allocate_min_grant_floor_for_kept_branches():
    branches = [("A", 0.99), ("B", 0.01)]
    alloc = asr.allocate(branches, 10_000, min_grant=200)
    assert alloc.grants["B"] >= 200                    # trailing-but-kept branch still probed
    assert alloc.grants["A"] > alloc.grants["B"]

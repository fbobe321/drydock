#!/usr/bin/env python3
"""baseline_report.py — aggregate the honest single-attempt baseline with a CI.

Reports the number that makes the result publishable: the DELTA between what the model
does unaided (one attempt, no score feedback) and what verifier-guided test-time search
achieves (the campaign's 63/89). Uses a Wilson interval, which is well-behaved at the
small n and extreme proportions a binomial normal-approximation handles badly.
"""
from __future__ import annotations

import csv
import math
import os
import sys

CSV = sys.argv[1] if len(sys.argv) > 1 else \
    "/data3/tbench_local/frontier/selfdistill/baseline/baseline_results.csv"
CAMPAIGN_SOLVED, CAMPAIGN_TOTAL = 63, 89   # verifier-guided search result


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def main() -> int:
    if not os.path.exists(CSV):
        print(f"no results yet: {CSV}")
        return 1
    rows = [r for r in csv.DictReader(open(CSV)) if r.get("task")]
    if not rows:
        print("no rows yet")
        return 1
    # per-task solve rate across attempts, then the task-level mean (the leaderboard shape)
    per: dict[str, list[int]] = {}
    for r in rows:
        per.setdefault(r["task"], []).append(int(r["solved"]))
    solved_any = sum(1 for v in per.values() if any(v))
    rate = sum(sum(v) / len(v) for v in per.values()) / len(per)
    n_tasks, n_runs = len(per), len(rows)
    lo, hi = wilson(round(rate * n_tasks), n_tasks)

    print(f"\n=== HONEST SINGLE-ATTEMPT BASELINE (no ratchet, no score feedback) ===")
    print(f"  tasks measured : {n_tasks}   runs: {n_runs}")
    print(f"  solve rate     : {rate:.1%}   95% CI [{lo:.1%}, {hi:.1%}]")
    print(f"  solved >=1 of k: {solved_any}/{n_tasks}")
    print(f"\n=== vs VERIFIER-GUIDED SEARCH (the campaign) ===")
    camp = CAMPAIGN_SOLVED / CAMPAIGN_TOTAL
    print(f"  search         : {CAMPAIGN_SOLVED}/{CAMPAIGN_TOTAL} = {camp:.1%}")
    print(f"  LIFT           : {camp - rate:+.1%}  ({camp / rate:.2f}x)" if rate else "  LIFT: n/a")
    print("\n  Claim shape: 'Drydock + verifier-guided test-time search lifts "
          f"Gemma-4-31B from {rate:.0%} to {camp:.0%} on Terminal-Bench 2.1.'")
    print("  NOTE: the search figure is NOT leaderboard-comparable (score feedback +"
          " ~48 attempts/task); the baseline IS the comparable quantity.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

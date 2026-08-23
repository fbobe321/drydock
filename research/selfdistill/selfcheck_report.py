#!/usr/bin/env python3
"""selfcheck_report.py — does an agent-authored scorecard agree with ground truth?

Reports the confusion matrix between a self-authored check and the official tbench
checker. The headline is the FALSE-PASS rate: how often the self-check says "done"
while the real checker says otherwise. That is precisely the risk of using a
self-authored target as a search objective — search optimizes proxies ruthlessly, so a
high false-pass rate means the ratchet would drive toward confident wrongness on
everyday (no-benchmark) tasks.
"""
from __future__ import annotations

import csv
import os
import sys

CSV = sys.argv[1] if len(sys.argv) > 1 else \
    "/data3/tbench_local/frontier/selfdistill/selfcheck/selfcheck_results.csv"


def main() -> int:
    if not os.path.exists(CSV):
        print(f"no results yet: {CSV}")
        return 1
    rows = [r for r in csv.DictReader(open(CSV)) if r.get("task")]
    usable = [r for r in rows if r["self_pass"] in ("0", "1")]
    no_check = len(rows) - len(usable)
    if not usable:
        print(f"no usable rows yet ({no_check} tasks produced no check)")
        return 1
    tt = ff = fp = fn = 0
    for r in usable:
        s, t = int(r["self_pass"]), int(r["true_pass"])
        if s and t: tt += 1
        elif not s and not t: ff += 1
        elif s and not t: fp += 1
        else: fn += 1
    n = len(usable)
    agree = tt + ff
    print("\n=== AGENT-AUTHORED SCORECARD vs GROUND TRUTH ===")
    print(f"  tasks with a usable check : {n}   (no check produced: {no_check})")
    print(f"  agreement                 : {agree}/{n} = {agree/n:.0%}")
    print(f"    both pass               : {tt}")
    print(f"    both fail               : {ff}")
    print(f"  FALSE-PASS (self✓ true✗)  : {fp}/{n} = {fp/n:.0%}   <-- the dangerous one")
    print(f"  false-fail (self✗ true✓)  : {fn}/{n} = {fn/n:.0%}   (conservative, safer)")
    print("\n  Interpretation: false-pass is the ceiling on trusting a self-authored")
    print("  check as a SEARCH TARGET. Low false-pass => the ratchet generalizes to")
    print("  everyday tasks. High false-pass => search would optimize into confident")
    print("  wrongness, and a human must approve the check before search runs.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Audit recent HLE Math failures and check whether auto-solve
fired on any of them. Designed to run hourly via cron to surface
adoption patterns and missing detector cases.

Output: one-line summary to stdout + a structured snapshot to
/tmp/auto_solve_audit.jsonl (one record per run).

Usage:
    python3 scripts/auto_solve_audit.py           # last 12 hours
    python3 scripts/auto_solve_audit.py --hours 1
    python3 scripts/auto_solve_audit.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/data3/drydock")

LOG_DIR = Path("/data3/drydock/logs")
TELE_PATH = Path("/tmp/auto_solve.jsonl")
SNAPSHOT_PATH = Path("/tmp/auto_solve_audit.jsonl")


def _recent_burndown_logs(hours: float) -> list[Path]:
    cutoff = time.time() - hours * 3600
    paths = []
    for p in LOG_DIR.glob("hle_burndown_*.log"):
        if p.stat().st_mtime >= cutoff:
            paths.append(p)
    return sorted(paths)


def _parse_results(log_path: Path) -> list[dict]:
    text = log_path.read_text(errors="replace")
    out = []
    for m in re.finditer(
        r"\[\d+/\d+\]\s+([a-f0-9]+)\s+\(([^)]+)\)\s*\n"
        r"\s*Q:\s+(.+?)\n"
        r"\s*pred:\s*(.*?)\n"
        r"\s*gold:\s+(.+?)\n"
        r"\s*→\s+(\w+)",
        text, re.DOTALL,
    ):
        qid, cat, q, pred, gold, verdict = m.groups()
        out.append({
            "qid": qid, "category": cat.strip(),
            "q": q.strip()[:400], "verdict": verdict,
        })
    return out


def _telemetry_records(hours: float) -> list[dict]:
    if not TELE_PATH.is_file():
        return []
    cutoff = time.time() - hours * 3600
    out = []
    try:
        with TELE_PATH.open() as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("ts", 0) >= cutoff:
                    out.append(r)
    except OSError:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    logs = _recent_burndown_logs(args.hours)
    results = []
    for lp in logs:
        results.extend(_parse_results(lp))
    tele = _telemetry_records(args.hours)

    # Detector firing rate on Math failures
    try:
        from drydock.core.constraint_hint import detect_constraint_shape
        from drydock.core.constraint_extract import extract
    except Exception as e:
        print(f"warn: could not import constraint modules: {e}", file=sys.stderr)
        detect_constraint_shape = None
        extract = None

    math_wrong = [r for r in results
                  if r["category"] == "Math" and r["verdict"] == "NO"]
    detector_hits = 0
    extractor_hits = 0
    if detect_constraint_shape and extract:
        for r in math_wrong:
            hit = detect_constraint_shape(r["q"])
            if hit:
                detector_hits += 1
                ext = extract(r["q"])
                if ext is not None:
                    extractor_hits += 1

    # Telemetry rollup
    tele_counter: Counter = Counter()
    injected_by_pred: Counter = Counter()
    z3_failures: list[dict] = []
    for r in tele:
        ev = r.get("event", "?")
        tele_counter[ev] += 1
        if ev == "injected":
            injected_by_pred[r.get("predicate", "?")] += 1
        if ev in ("z3_failed", "z3_non_actionable", "formula_not_z3_friendly"):
            z3_failures.append(r)

    snapshot = {
        "ts": time.time(),
        "window_hours": args.hours,
        "questions_completed": len(results),
        "by_verdict": dict(Counter(r["verdict"] for r in results)),
        "math_wrong": len(math_wrong),
        "math_wrong_detector_hits": detector_hits,
        "math_wrong_extractor_hits": extractor_hits,
        "auto_solve_events": dict(tele_counter),
        "auto_solve_injected_by_predicate": dict(injected_by_pred),
        "auto_solve_failures_sample": z3_failures[:5],
    }

    try:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with SNAPSHOT_PATH.open("a") as f:
            f.write(json.dumps(snapshot) + "\n")
    except OSError as e:
        print(f"warn: could not write snapshot: {e}", file=sys.stderr)

    if args.json:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
        return 0

    # Plain summary
    print(f"=== auto-solve audit ({args.hours}h window) ===")
    print(f"Questions answered:  {len(results)}")
    print(f"  verdicts:          {dict(Counter(r['verdict'] for r in results))}")
    print(f"Math wrong answers:  {len(math_wrong)}")
    if math_wrong:
        print(f"  detector matched: {detector_hits}/{len(math_wrong)}"
              f" ({100*detector_hits//max(1,len(math_wrong))}%)")
        print(f"  extractor fired:  {extractor_hits}/{len(math_wrong)}"
              f" ({100*extractor_hits//max(1,len(math_wrong))}%)")
    print()
    print("Auto-solve telemetry:")
    if not tele:
        print("  (no events in window)")
    else:
        for ev, n in tele_counter.most_common():
            print(f"  {ev:28s}  {n}")
        if injected_by_pred:
            print()
            print("  Injected by predicate:")
            for pred, n in injected_by_pred.most_common():
                print(f"    {pred:22s}  {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

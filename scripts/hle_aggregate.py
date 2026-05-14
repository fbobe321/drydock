#!/usr/bin/env python3
"""Multi-batch HLE results aggregator.

Walks `/data3/drydock/hle_results/run_<ts>/` directories and produces
a single rollup:

  - total questions attempted
  - correct count + score
  - per-category breakdown
  - per-method breakdown (exact / fuzzy / judge YES / empty:no_response /
    empty:no_final_answer / ERROR)
  - per-batch summary line

Useful when the hourly babysitter has been firing for a while and the
operator wants "where are we" without re-reading every run_* dir.

CLI:

    # Summarize everything ever produced (default)
    python3 scripts/hle_aggregate.py

    # Only since a date
    python3 scripts/hle_aggregate.py --since 2026-05-14

    # JSON output for downstream tools
    python3 scripts/hle_aggregate.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


RESULTS_ROOT = Path("/data3/drydock/hle_results")


def _iter_runs(since: datetime | None) -> list[Path]:
    runs: list[Path] = []
    if not RESULTS_ROOT.is_dir():
        return runs
    for d in RESULTS_ROOT.iterdir():
        if not d.is_dir() or not d.name.startswith("run_"):
            continue
        if since is not None:
            try:
                # Run dir mtime is a good enough proxy for run-start time.
                mtime = datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if mtime < since:
                continue
        runs.append(d)
    runs.sort(key=lambda p: p.stat().st_mtime)
    return runs


def _read_results(run: Path) -> list[dict]:
    """Prefer rejudged.jsonl when present — that file carries the
    post-bc12eee-judge-fix verdicts and is the source of truth once
    the operator has run `scripts/rejudge_hle.py --apply`. Falls back
    to results.jsonl for runs that haven't been rejudged."""
    rejudged = run / "rejudged.jsonl"
    f = rejudged if rejudged.is_file() else (run / "results.jsonl")
    if not f.is_file():
        return []
    out = []
    for line in f.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def aggregate(runs: list[Path]) -> dict:
    total = 0
    correct = 0
    by_category: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "correct": 0})
    by_method: Counter = Counter()
    per_batch: list[dict] = []

    for run in runs:
        rows = _read_results(run)
        if not rows:
            continue
        b_total = len(rows)
        b_correct = sum(1 for r in rows if r.get("correct"))
        per_batch.append({
            "run": run.name,
            "mtime": run.stat().st_mtime,
            "total": b_total,
            "correct": b_correct,
            "score": (b_correct / b_total) if b_total else 0.0,
        })
        for r in rows:
            total += 1
            if r.get("correct"):
                correct += 1
            cat = r.get("category") or "?"
            by_category[cat]["total"] += 1
            if r.get("correct"):
                by_category[cat]["correct"] += 1
            by_method[r.get("method") or "(none)"] += 1

    return {
        "runs": len(runs),
        "total": total,
        "correct": correct,
        "score": (correct / total) if total else 0.0,
        "by_category": {
            k: {**v, "score": (v["correct"] / v["total"]) if v["total"] else 0.0}
            for k, v in by_category.items()
        },
        "by_method": dict(by_method),
        "per_batch": per_batch,
    }


def _format_text(agg: dict) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("HLE multi-batch rollup")
    lines.append("=" * 60)
    lines.append(f"runs: {agg['runs']}")
    lines.append(f"total: {agg['total']}   correct: {agg['correct']}   "
                 f"score: {agg['score']*100:.1f}%")
    lines.append("")
    lines.append("by category:")
    cats = sorted(agg["by_category"].items(), key=lambda kv: -kv[1]["total"])
    for cat, v in cats:
        lines.append(f"  {cat:<20s}  {v['correct']:>4d}/{v['total']:<4d}  "
                     f"{v['score']*100:.1f}%")
    lines.append("")
    lines.append("by method:")
    for m, n in agg["by_method"].items():
        lines.append(f"  {m:<24s}  {n:>5d}")
    lines.append("")
    lines.append("recent batches:")
    for b in agg["per_batch"][-10:]:
        dt = datetime.fromtimestamp(b["mtime"], tz=timezone.utc).strftime("%m-%d %H:%M")
        lines.append(f"  {dt}  {b['run']:<22s}  "
                     f"{b['correct']:>2d}/{b['total']:<3d} = {b['score']*100:>5.1f}%")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=str, default=None,
                    help="UTC date YYYY-MM-DD; only include batches at or after this.")
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON instead of text rollup.")
    args = ap.parse_args(argv)

    since = None
    if args.since:
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"bad --since: {args.since!r}", file=sys.stderr)
            return 2

    runs = _iter_runs(since)
    agg = aggregate(runs)

    if args.json:
        print(json.dumps(agg, indent=2, default=str))
    else:
        print(_format_text(agg))
    return 0


if __name__ == "__main__":
    sys.exit(main())

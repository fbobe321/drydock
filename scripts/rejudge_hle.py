#!/usr/bin/env python3
"""Re-judge HLE results that previously hit `verdict=ERROR`.

The judge in `scripts/hle_eval.py` had an IndexError bug across the
entire history of HLE eval (fixed 2026-05-14 in commit bc12eee):
every single judge invocation (22/22 in 165 results) returned
ERROR because `text.splitlines()[0]` raised on an empty `content`
field. The 5/163 = 3.1% lifetime score under-counts whatever Q3 / Q4
actually answered correctly.

This script:
  - Walks `/data3/drydock/hle_results/run_*/results.jsonl`
  - Picks rows where `verdict == "ERROR"` AND `predicted` is non-empty
  - Calls the fixed `judge_with_gemma` on each (question, gold, pred)
  - Writes a rejudged copy alongside the original
  - Reports the new verdict distribution

Doesn't modify the originals (each run dir gets a `rejudged.jsonl`
sibling). Pass `--apply-to-summary` to also rewrite each run's
`summary.json` with the new correct counts.

Usage:
    python3 scripts/rejudge_hle.py                          # dry run
    python3 scripts/rejudge_hle.py --since 2026-05-13       # only newer runs
    python3 scripts/rejudge_hle.py --apply --apply-to-summary
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/data3/drydock")
RESULTS_ROOT = REPO / "hle_results"


def _load_hle_eval():
    """Import scripts/hle_eval.py for its judge_with_gemma."""
    src = REPO / "scripts" / "hle_eval.py"
    spec = importlib.util.spec_from_file_location("hle_eval", src)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hle_eval"] = mod
    spec.loader.exec_module(mod)
    return mod


def _iter_runs(since: datetime | None):
    if not RESULTS_ROOT.is_dir():
        return []
    out = []
    for d in RESULTS_ROOT.iterdir():
        if not (d.is_dir() and d.name.startswith("run_")):
            continue
        if since is not None:
            try:
                m = datetime.fromtimestamp(d.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if m < since:
                continue
        out.append(d)
    out.sort(key=lambda p: p.stat().st_mtime)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="Write rejudged.jsonl files (default: dry run).")
    ap.add_argument("--apply-to-summary", action="store_true",
                    help="Also rewrite per-run summary.json with new counts.")
    ap.add_argument("--since", default=None,
                    help="UTC date YYYY-MM-DD; skip older runs.")
    args = ap.parse_args(argv)

    since = None
    if args.since:
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"bad --since: {args.since!r}", file=sys.stderr)
            return 2

    he = _load_hle_eval()
    runs = _iter_runs(since)
    total_runs = 0
    total_attempted = 0
    total_changed: Counter[str] = Counter()
    new_score_delta = 0

    print(f"{'run':<28s} {'rejudged':>9s} {'YES':>4s} {'NO':>4s} {'PART':>4s} {'ERR':>4s}")

    for run in runs:
        results_path = run / "results.jsonl"
        if not results_path.is_file():
            continue
        rows = []
        for line in results_path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if not rows:
            continue
        total_runs += 1

        verdicts: Counter[str] = Counter()
        rejudged_rows = []
        any_changed = False
        for r in rows:
            new = dict(r)
            should_rejudge = (
                r.get("verdict") == "ERROR"
                and (r.get("predicted") or "").strip()
                and r.get("ground_truth")
            )
            if should_rejudge:
                v, reasoning = he.judge_with_gemma(
                    r.get("question", "?"),
                    r["ground_truth"],
                    r["predicted"],
                )
                verdicts[v] += 1
                total_attempted += 1
                if v != "ERROR":
                    any_changed = True
                    total_changed[v] += 1
                    new["verdict"] = v
                    new["correct"] = (v == "YES")
                    new["partial"] = (v == "PARTIAL")
                    new["method"] = "judge_rerun"
                    new["judge_reasoning"] = reasoning
                    new["original_verdict"] = "ERROR"
                    if v == "YES" and not r.get("correct"):
                        new_score_delta += 1
            rejudged_rows.append(new)

        # Per-run summary.
        print(f"{run.name:<28s} {sum(verdicts.values()):>9d} "
              f"{verdicts['YES']:>4d} {verdicts['NO']:>4d} "
              f"{verdicts['PARTIAL']:>4d} {verdicts['ERROR']:>4d}")

        if args.apply:
            out = run / "rejudged.jsonl"
            out.write_text("\n".join(json.dumps(r) for r in rejudged_rows) + "\n")

            if args.apply_to_summary and any_changed:
                # Rebuild summary.json
                total = len(rejudged_rows)
                correct = sum(1 for r in rejudged_rows if r.get("correct"))
                by_cat: dict[str, dict[str, int]] = {}
                for r in rejudged_rows:
                    cat = r.get("category") or "?"
                    d = by_cat.setdefault(cat, {"total": 0, "correct": 0})
                    d["total"] += 1
                    if r.get("correct"):
                        d["correct"] += 1
                for d in by_cat.values():
                    d["score"] = (d["correct"] / d["total"]) if d["total"] else 0.0
                summary = {
                    "total": total, "correct": correct,
                    "score": (correct / total) if total else 0.0,
                    "by_category": by_cat,
                    "run_dir": str(run),
                    "rejudged_at": datetime.now(timezone.utc).isoformat(),
                }
                (run / "summary.json").write_text(json.dumps(summary, indent=4))

    print()
    print(f"runs scanned:    {total_runs}")
    print(f"rejudged:        {total_attempted}")
    print(f"verdict changes: {sum(total_changed.values())}")
    print(f"  new YES:       {total_changed['YES']}")
    print(f"  new NO:        {total_changed['NO']}")
    print(f"  new PARTIAL:   {total_changed['PARTIAL']}")
    print(f"score delta:     +{new_score_delta} (questions that flipped from "
          "ERROR/incorrect to YES)")

    if not args.apply:
        print("\n(dry run — pass --apply to write rejudged.jsonl files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Backfill HLE failures into ~/.drydock/dispatch/curiosity.jsonl.

The HLE harness writes curiosity items inline now (post-v2.8.20), but
prior runs are already on disk and represent real learning signal.
This script walks every `results.jsonl` under `/data3/drydock/hle_results/`
and enqueues a CuriosityItem for each NO outcome it finds.

Dedup is handled by the queue's 7-day fingerprint window:
- a run from May 5 (already > 7 days old) backfills fresh
- a run from today is dedup'd against any inline writes from the harness

Usage:
    python3 scripts/backfill_hle_curiosity.py             # dry run
    python3 scripts/backfill_hle_curiosity.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path("/data3/drydock")
RESULTS_ROOT = REPO / "hle_results"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually enqueue (default: dry run)")
    args = ap.parse_args()

    if not RESULTS_ROOT.is_dir():
        print(f"no results dir at {RESULTS_ROOT}", file=sys.stderr)
        return 1

    # Late import so a syntax-broken curiosity module never blocks dry-runs.
    if args.apply:
        sys.path.insert(0, str(REPO))
        from drydock.curiosity import CuriosityItem, CuriosityKind, enqueue

    runs = sorted(p for p in RESULTS_ROOT.iterdir()
                  if p.is_dir() and p.name.startswith("run_"))
    if not runs:
        print(f"no run dirs under {RESULTS_ROOT}", file=sys.stderr)
        return 1

    total_results = 0
    total_no = 0
    total_enqueued = 0
    total_deduped = 0

    for run in runs:
        rfile = run / "results.jsonl"
        if not rfile.is_file():
            continue
        with rfile.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total_results += 1
                if r.get("correct"):
                    continue
                total_no += 1
                qid = r.get("id", "?")
                method = r.get("method", "")
                judge_r = r.get("judge_reasoning", "")
                category = r.get("category", "")
                if not args.apply:
                    continue
                wrote = enqueue(CuriosityItem(
                    kind=CuriosityKind.HLE_FAILURE,
                    term=(r.get("ground_truth", "")
                          or r.get("predicted", "") or qid)[:200],
                    context=(
                        f"Predicted: {r.get('predicted', '')[:200]}\n"
                        f"Gold: {r.get('ground_truth', '')[:200]}\n"
                        f"Judge: {judge_r[:200]}\n"
                        f"Category: {category}"
                    ),
                    source=f"hle:{qid}",
                    suggested_action=(
                        "Investigate retrieval coverage for this topic; "
                        "consider GraphRAG ingest of relevant arXiv slice "
                        "or a prompt rule to force retrieve before answering."
                        if method == "empty"
                        else "Compare predicted vs gold; if it's a recurring "
                             "category-level bias, propose a prompt or "
                             "AGENTS.md update."
                    ),
                    confidence=0.9 if method == "empty" else 0.6,
                    extra={"category": category, "method": method,
                           "run": run.name},
                ))
                if wrote:
                    total_enqueued += 1
                else:
                    total_deduped += 1

    print(f"runs scanned:      {len(runs)}")
    print(f"total results:     {total_results}")
    print(f"NO outcomes:       {total_no}")
    if args.apply:
        print(f"enqueued:          {total_enqueued}")
        print(f"deduped:           {total_deduped}")
    else:
        print(f"(dry run — pass --apply to enqueue {total_no} items)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

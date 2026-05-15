#!/usr/bin/env python3
"""HLE burndown status — single-page summary across all batches.

Used for the morning check-in. Counts questions completed across the
full burndown history, correctness by category, time-per-question
distribution, stall vs answered rate, and whether the `solve` /
`math` / other symbolic tools are getting picked up.

Usage:
    python3 scripts/hle_burndown_status.py             # all batches
    python3 scripts/hle_burndown_status.py --since 2026-05-15
    python3 scripts/hle_burndown_status.py --json      # machine-readable
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path("/data3/drydock/logs")
LOG_GLOB = "hle_burndown_*.log"
# Legacy babysitter logs follow the same shape — include them.
LEGACY_GLOB = "hle_continuous_*.log"


def _parse_log(log_path: Path) -> list[dict]:
    """Extract per-question records from a single batch log.

    Returns one dict per question: {qid, category, q, pred, gold,
    verdict, elapsed_sec, msgs, file}.
    """
    text = log_path.read_text(errors="replace")
    cat_m = re.search(r"category=(\S+)\b", text) or re.search(
        r"category '([^']+)'", text)
    category = cat_m.group(1).strip(" '\"") if cat_m else "unknown"

    out = []
    # Block format from hle_eval.py:
    #   [N/M] <qid>  (<Category>)
    #     Q: ...
    #     pred: ...
    #     gold: ...
    #     → VERDICT  (reason, <elapsed>s, <N> msgs)
    pattern = re.compile(
        r"\[\d+/\d+\]\s+([a-f0-9]+)\s+\(([^)]+)\)\s*\n"
        r"\s*Q:\s+(.+?)\n"
        r"\s*pred:\s*(.*?)\n"
        r"\s*gold:\s+(.+?)\n"
        r"\s*→\s+(\w+)\s+\(([^)]+)\)",
        re.DOTALL,
    )
    for m in pattern.finditer(text):
        qid, cat, q, pred, gold, verdict, extra = m.groups()
        # extra looks like "judge, 274s, 6 msgs" or "exact, 274s, 4 msgs"
        elapsed = None
        msgs = None
        elapsed_m = re.search(r"(\d+)s", extra)
        if elapsed_m:
            elapsed = int(elapsed_m.group(1))
        msgs_m = re.search(r"(\d+)\s*msgs?", extra)
        if msgs_m:
            msgs = int(msgs_m.group(1))
        out.append({
            "qid": qid,
            "category": cat.strip(),
            "q": q.strip()[:300],
            "pred": pred.strip()[:50],
            "gold": gold.strip()[:50],
            "verdict": verdict,
            "elapsed_sec": elapsed,
            "msgs": msgs,
            "file": log_path.name,
        })
    return out


def _scan_tool_usage(qids: list[str]) -> dict:
    """For a set of qids, count which tools were called per session.

    Maps qid → session_dir via the hle_eval results.jsonl files
    under /data3/drydock/hle_results/run_*/. Each result row
    records the session_dir used for that question, so the lookup
    is direct (no fragile head-scan of messages.jsonl).
    """
    results_root = Path("/data3/drydock/hle_results")
    if not results_root.is_dir():
        return {}
    qid_set = set(qids)
    qid_to_session: dict[str, Path] = {}
    for run_dir in results_root.glob("run_*"):
        rf = run_dir / "results.jsonl"
        if not rf.is_file():
            continue
        try:
            with rf.open() as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    qid = r.get("id")
                    sd = r.get("session_dir")
                    if qid in qid_set and sd:
                        qid_to_session[qid] = Path(sd)
        except OSError:
            continue

    tool_counts: Counter = Counter()
    sessions_using: Counter = Counter()
    for qid, sd in qid_to_session.items():
        msg_file = sd / "messages.jsonl"
        if not msg_file.is_file():
            continue
        local: Counter = Counter()
        try:
            with msg_file.open() as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for tc in (r.get("tool_calls") or []):
                        fn = tc.get("function") or {}
                        name = fn.get("name") or tc.get("name")
                        if name:
                            local[name] += 1
        except OSError:
            continue
        tool_counts.update(local)
        for tool_name in local:
            sessions_using[tool_name] += 1
    return {
        "counts": dict(tool_counts),
        "sessions": dict(sessions_using),
        "sessions_resolved": len(qid_to_session),
    }


def _format_table(rows: list[tuple]) -> str:
    if not rows:
        return ""
    cols = list(zip(*rows))
    widths = [max(len(str(v)) for v in col) for col in cols]
    lines = []
    for row in rows:
        lines.append("  " + "  ".join(
            str(v).ljust(w) for v, w in zip(row, widths)))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="",
                    help="Only logs on/after this UTC date (YYYY-MM-DD)")
    ap.add_argument("--json", action="store_true",
                    help="Machine-readable JSON instead of report")
    ap.add_argument("--include-legacy", action="store_true",
                    help="Also include hle_continuous_*.log (babysitter logs)")
    args = ap.parse_args()

    since_dt = None
    if args.since:
        since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(
            tzinfo=timezone.utc)

    paths = list(LOG_DIR.glob(LOG_GLOB))
    if args.include_legacy:
        paths.extend(LOG_DIR.glob(LEGACY_GLOB))
    if since_dt:
        keep = []
        for p in paths:
            # Filename: hle_*_YYYYMMDD_HHMMSS.log
            m = re.search(r"_(\d{8})_\d{6}\.log$", p.name)
            if m:
                try:
                    d = datetime.strptime(m.group(1), "%Y%m%d").replace(
                        tzinfo=timezone.utc)
                except ValueError:
                    continue
                if d >= since_dt:
                    keep.append(p)
        paths = keep

    records = []
    for p in sorted(paths):
        records.extend(_parse_log(p))

    if not records:
        print("no batches found", file=sys.stderr)
        return 1

    # Aggregate
    by_cat: dict[str, list] = defaultdict(list)
    for r in records:
        by_cat[r["category"]].append(r)

    if args.json:
        out = {
            "total_questions": len(records),
            "batches": len(paths),
            "by_category": {
                cat: {
                    "total": len(rs),
                    "correct": sum(1 for r in rs if r["verdict"] in ("YES", "PARTIAL")),
                    "stalled": sum(1 for r in rs if r["pred"] in ("", "None")),
                }
                for cat, rs in by_cat.items()
            },
        }
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    # Plain-text report
    print(f"=== HLE Burndown Summary ===")
    print(f"Logs scanned:       {len(paths)}")
    print(f"Questions answered: {len(records)}")
    if since_dt:
        print(f"Since:              {args.since}")
    print()

    # Correctness by category
    rows = [("Category", "Total", "Correct", "%", "Stalled", "Avg time")]
    rows.append(("-" * 24, "-----", "-------", "----", "-------", "--------"))
    for cat in sorted(by_cat, key=lambda c: -len(by_cat[c])):
        rs = by_cat[cat]
        n = len(rs)
        correct = sum(1 for r in rs if r["verdict"] in ("YES", "PARTIAL"))
        stalled = sum(1 for r in rs if (r["pred"] or "") in ("", "None"))
        times = [r["elapsed_sec"] for r in rs if r["elapsed_sec"] is not None]
        avg_t = f"{sum(times)//len(times)}s" if times else "?"
        rows.append((cat[:24], n, correct, f"{100*correct//max(1,n)}%",
                     stalled, avg_t))
    rows.append(("-" * 24, "-----", "-------", "----", "-------", "--------"))
    total = len(records)
    total_correct = sum(1 for r in records if r["verdict"] in ("YES", "PARTIAL"))
    total_stalled = sum(1 for r in records if (r["pred"] or "") in ("", "None"))
    times = [r["elapsed_sec"] for r in records if r["elapsed_sec"] is not None]
    avg_t = f"{sum(times)//len(times)}s" if times else "?"
    rows.append(("TOTAL", total, total_correct,
                 f"{100*total_correct//max(1,total)}%",
                 total_stalled, avg_t))
    print(_format_table(rows))
    print()

    # Tool usage (slow — only sampled qids)
    qids = [r["qid"] for r in records[-200:]]  # last 200
    print(f"=== Tool usage (last {len(qids)} sessions) ===")
    tu = _scan_tool_usage(qids)
    if tu.get("counts"):
        # Sort by call count
        items = sorted(tu["counts"].items(), key=lambda x: -x[1])
        for name, calls in items[:15]:
            sess = tu["sessions"].get(name, 0)
            mark = ""
            if name == "solve":
                mark = "  ← Z3 constraint solver (smart-template target)"
            elif name in ("logic", "algebra", "number_theory", "set",
                          "linear_algebra", "stats", "units", "chemistry"):
                mark = "  ← symbolic stack"
            print(f"  {name:24s}  {calls:5d} calls  {sess:3d} sessions{mark}")
    else:
        print("  (no tool data found)")

    # Stalled sessions are the dominant failure mode — flag if >30%
    if total_stalled / max(1, total) > 0.3:
        print()
        print(f"⚠  {100*total_stalled//total}% of questions stalled "
              "(empty prediction). Check thinking-budget / tool-stop-after / "
              "time-budget settings.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

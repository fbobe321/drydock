#!/usr/bin/env python3
"""Session loop auditor — post-mortem analysis of drydock sessions.

Answers the question I missed during testing: how many times did the
model repeat identical tool calls, and how did the harness respond?

Usage:
    python3 scripts/session_loop_audit.py <session_id_or_path>
    python3 scripts/session_loop_audit.py --recent N     # analyze last N sessions
    python3 scripts/session_loop_audit.py --all-today    # everything from today
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


SESSION_ROOT = Path.home() / ".vibe/logs/session"


def hash_tool_call(tc: dict) -> str:
    fn = tc.get("function", {}).get("name", "?")
    args = tc.get("function", {}).get("arguments", "")
    if isinstance(args, dict):
        args = json.dumps(args, sort_keys=True)
    # Truncate very long args (write_file content) but keep enough to
    # distinguish near-identical calls.
    return f"{fn}:{str(args)[:300]}"


def analyze(session_dir: Path) -> dict:
    """Compute loop statistics for one session."""
    jsonl = session_dir / "messages.jsonl"
    if not jsonl.exists():
        return {"session": session_dir.name, "error": "no messages.jsonl"}

    tool_calls = []
    tool_results = []  # list of (tool_name, was_error, short_summary)
    system_notes = 0
    nudge_occurrences = 0

    for line in jsonl.read_text().splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = d.get("role")
        if role == "assistant" and d.get("tool_calls"):
            for tc in d["tool_calls"]:
                fn = tc.get("function", {}).get("name", "?")
                tool_calls.append({
                    "key": hash_tool_call(tc),
                    "name": fn,
                })
        elif role == "tool":
            content = d.get("content", "") or ""
            if isinstance(content, list):
                content = json.dumps(content)
            is_error = (
                "tool_error" in content.lower() or
                "failed" in content.lower() or
                "not found" in content.lower() or
                "error" in content.lower()[:50]
            )
            tool_results.append({
                "name": d.get("name", "?"),
                "error": is_error,
                "summary": str(content)[:80],
            })
        elif role == "system":
            system_notes += 1
            content = str(d.get("content", ""))
            if "STOP: You are calling" in content or "You have run very similar" in content:
                nudge_occurrences += 1

    total = len(tool_calls)
    counts = Counter(tc["key"] for tc in tool_calls)
    repeats = [(k, n) for k, n in counts.items() if n >= 3]
    repeats.sort(key=lambda x: -x[1])

    # Repeat runs: find consecutive identical tool calls (harder loop signal)
    consecutive_max = 0
    run = 0
    prev = None
    for tc in tool_calls:
        if tc["key"] == prev:
            run += 1
            consecutive_max = max(consecutive_max, run)
        else:
            run = 1
            prev = tc["key"]
    consecutive_max = max(consecutive_max, run)

    # Error fraction on tool calls
    err_count = sum(1 for r in tool_results if r["error"])
    err_frac = err_count / max(1, len(tool_results))

    return {
        "session": session_dir.name,
        "tool_calls_total": total,
        "tool_calls_unique": len(counts),
        "dup_ratio": round(1 - len(counts) / max(1, total), 2),
        "consecutive_max": consecutive_max,
        "loops_ge3": len(repeats),
        "loops_worst": repeats[:3],
        "tool_error_frac": round(err_frac, 2),
        "system_notes": system_notes,
        "nudges_fired": nudge_occurrences,
    }


def print_one(report: dict, verbose: bool = False) -> None:
    s = report
    if "error" in s:
        print(f"{s['session']}: {s['error']}")
        return
    flag = ""
    if s["consecutive_max"] >= 5:
        flag = " 🚨 LOOP"
    elif s["dup_ratio"] >= 0.4:
        flag = " ⚠ high dup"
    elif s["tool_error_frac"] >= 0.5:
        flag = " ⚠ error-heavy"
    print(f"{s['session']}{flag}")
    print(f"  calls: {s['tool_calls_total']} ({s['tool_calls_unique']} unique, dup-ratio {s['dup_ratio']})")
    print(f"  worst consecutive: {s['consecutive_max']}")
    print(f"  loops ≥3:          {s['loops_ge3']}")
    print(f"  error fraction:    {s['tool_error_frac']}")
    print(f"  nudges fired:      {s['nudges_fired']} (of {s['system_notes']} system notes)")
    if verbose and s.get("loops_worst"):
        for key, n in s["loops_worst"]:
            print(f"    {n}x {key[:140]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", help="session dir or id")
    ap.add_argument("--recent", type=int, help="analyze N most recent")
    ap.add_argument("--all-today", action="store_true",
                    help="analyze every session created today")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--json", action="store_true",
                    help="emit JSON array")
    args = ap.parse_args()

    sessions: list[Path] = []
    if args.session:
        p = Path(args.session)
        if not p.exists():
            p = SESSION_ROOT / args.session
        if not p.exists():
            cand = sorted(SESSION_ROOT.glob(f"session_*{args.session}*"))
            if cand:
                p = cand[-1]
        if p.exists():
            sessions = [p]
    elif args.recent:
        sessions = sorted(SESSION_ROOT.iterdir(),
                          key=lambda x: x.stat().st_mtime)[-args.recent:]
    elif args.all_today:
        today = datetime.now().strftime("%Y%m%d")
        sessions = [d for d in sorted(SESSION_ROOT.iterdir())
                    if today in d.name]
    else:
        # Default: last 5
        sessions = sorted(SESSION_ROOT.iterdir(),
                          key=lambda x: x.stat().st_mtime)[-5:]

    reports = [analyze(d) for d in sessions]

    if args.json:
        print(json.dumps(reports, indent=2))
        return 0

    looping = [r for r in reports if r.get("consecutive_max", 0) >= 5]
    dup_heavy = [r for r in reports if r.get("dup_ratio", 0) >= 0.4
                 and r.get("consecutive_max", 0) < 5]

    for r in reports:
        print_one(r, verbose=args.verbose)
        print()

    if looping:
        print(f"=== {len(looping)} SESSIONS WITH SEVERE LOOPS ===")
        for r in looping:
            print(f"  {r['session']} (max consecutive {r['consecutive_max']})")
    if dup_heavy:
        print(f"=== {len(dup_heavy)} sessions with high duplication ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

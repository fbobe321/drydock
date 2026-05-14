#!/usr/bin/env python3
"""Tool-usage reporter — scan recent drydock sessions and tally how often
each tool was actually called by the model.

Useful for measuring whether new tools (math/count/memory/verify) are
getting picked up after a release, vs. just being defined and ignored.

Sessions live at ~/.vibe/logs/session/session_*/messages.jsonl.

Usage:
    python3 scripts/tool_usage_report.py                   # last 50 sessions
    python3 scripts/tool_usage_report.py --limit 200       # last 200 sessions
    python3 scripts/tool_usage_report.py --since 2026-05-12 --tool math
    python3 scripts/tool_usage_report.py --json            # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

def _session_roots() -> list[Path]:
    """Return all session-log roots that exist on disk.

    Drydock's active root is `~/.drydock/logs/session` (post the
    config-save_dir cleanup on 2026-05-14). Older sessions still live
    at `~/.vibe/logs/session` from before the migration; we still
    read those so historical analyses don't lose ~12k sessions.
    """
    candidates = [
        Path.home() / ".drydock" / "logs" / "session",
        Path.home() / ".vibe" / "logs" / "session",
    ]
    return [r for r in candidates if r.is_dir()]


# Back-compat: kept as the first existing root so legacy callers that
# read this constant get a sensible value. New code should call
# `_session_roots()` directly.
_roots = _session_roots()
SESSION_ROOT = _roots[0] if _roots else Path.home() / ".drydock" / "logs" / "session"


def _session_dirs(limit: int, since: datetime | None) -> list[Path]:
    roots = _session_roots()
    if not roots:
        return []
    all_dirs: list[Path] = []
    for r in roots:
        all_dirs.extend(p for p in r.glob("session_*") if p.is_dir())
    dirs = sorted(all_dirs, key=lambda p: p.name, reverse=True)
    if since is not None:
        # session_YYYYMMDD_HHMMSS_xxx
        keep: list[Path] = []
        for d in dirs:
            try:
                ts_str = d.name.split("_", 2)[1]
                day = datetime.strptime(ts_str, "%Y%m%d").replace(tzinfo=timezone.utc)
                if day >= since:
                    keep.append(d)
            except (ValueError, IndexError):
                pass
        dirs = keep
    return dirs[:limit]


def _scan_session(d: Path) -> Counter:
    """Count tool calls in this session, keyed by tool name."""
    msg_file = d / "messages.jsonl"
    if not msg_file.is_file():
        return Counter()
    counts: Counter = Counter()
    try:
        with msg_file.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Tool calls live in the assistant's tool_calls list.
                tcs = rec.get("tool_calls") or []
                for tc in tcs:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    name = fn.get("name") or tc.get("name") if isinstance(tc, dict) else None
                    if name:
                        counts[name] += 1
    except OSError:
        pass
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50,
                    help="Max sessions to scan (newest first)")
    ap.add_argument("--since", default="",
                    help="Only sessions on/after this UTC date (YYYY-MM-DD)")
    ap.add_argument("--tool", default="",
                    help="Filter to one tool name (still shows totals)")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON instead of table")
    args = ap.parse_args()

    since: datetime | None = None
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    dirs = _session_dirs(args.limit, since)
    if not dirs:
        print("no sessions found", file=sys.stderr)
        return 1

    total_counts: Counter = Counter()
    sessions_using: Counter = Counter()  # how many sessions called each tool ≥1
    for d in dirs:
        c = _scan_session(d)
        total_counts.update(c)
        for name in c:
            sessions_using[name] += 1

    symbolic_stack = ("logic", "algebra", "number_theory", "set",
                      "linear_algebra", "stats", "units", "chemistry")

    if args.json:
        out = {
            "sessions_scanned": len(dirs),
            "since": args.since or None,
            "tool_calls_total": dict(total_counts),
            "sessions_using_tool": dict(sessions_using),
            "symbolic_stack": {
                "tools": list(symbolic_stack),
                "calls": {t: total_counts.get(t, 0) for t in symbolic_stack},
                "sessions_using": {t: sessions_using.get(t, 0)
                                   for t in symbolic_stack},
            },
        }
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    print(f"Sessions scanned: {len(dirs)}")
    if since:
        print(f"Since: {args.since}")
    if args.tool:
        print(f"Filter: tool={args.tool}")
    print()
    print(f"{'tool':<30s} {'calls':>8s} {'sessions':>10s}")
    print("-" * 52)

    items = sorted(total_counts.items(), key=lambda x: (-x[1], x[0]))
    if args.tool:
        items = [(n, c) for (n, c) in items if n == args.tool]
    if not items:
        print("(no tool calls)")
        return 0
    for name, calls in items:
        sess = sessions_using[name]
        print(f"{name:<30s} {calls:>8d} {sess:>10d}")
    print()
    new_tools = ("math", "count", "memory", "verify")
    new_use = sum(total_counts.get(t, 0) for t in new_tools)
    new_sess = sum(1 for d in dirs if any(
        _scan_session(d).get(t, 0) for t in new_tools))
    print(f"NEW TOOLS (math/count/memory/verify):")
    print(f"  total calls:   {new_use}")
    print(f"  sessions used: {new_sess} / {len(dirs)}")

    sym_use = sum(total_counts.get(t, 0) for t in symbolic_stack)
    sym_sess = sum(1 for d in dirs if any(
        _scan_session(d).get(t, 0) for t in symbolic_stack))
    print()
    print("SYMBOLIC STACK (logic/algebra/number_theory/set/linear_algebra/"
          "stats/units/chemistry):")
    print(f"  total calls:   {sym_use}")
    print(f"  sessions used: {sym_sess} / {len(dirs)}")
    # Per-tool breakdown so we see which ones are dead weight vs. used.
    print(f"  {'tool':<18s} {'calls':>8s} {'sessions':>10s}")
    for t in symbolic_stack:
        print(f"    {t:<16s} {total_counts.get(t, 0):>8d} "
              f"{sessions_using.get(t, 0):>10d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

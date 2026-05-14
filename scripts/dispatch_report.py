#!/usr/bin/env python3
"""Trailing-window classifier signal report.

Reads `~/.drydock/dispatch/*.jsonl` and prints how many entries hit
each pattern within a configurable trailing window (default 12h).
Useful for `autonomous_review` and the operator to see what's
trending without grepping multi-megabyte queue files.

Output columns:
  pattern_id (40 chars), bucket, count, % of bucket total, latest ts

CLI:
    python3 scripts/dispatch_report.py               # 12h window
    python3 scripts/dispatch_report.py --window 1h
    python3 scripts/dispatch_report.py --window 7d
    python3 scripts/dispatch_report.py --json        # machine-readable
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


QUEUE_ROOT = Path.home() / ".drydock" / "dispatch"


def _parse_window(spec: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([smhdw])", spec.strip().lower())
    if not m:
        raise ValueError(f"bad window: {spec!r} (expect '1h', '12h', '7d', etc.)")
    n = int(m.group(1))
    unit = m.group(2)
    return {
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "w": timedelta(weeks=n),
    }[unit]


def _parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    # Tolerate both `2026-05-14T00:30:01Z` and `+00:00` suffixes.
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _walk_queue(path: Path, cutoff: datetime | None):
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_ts(r.get("ts") or "")
        if cutoff is not None and ts is not None and ts < cutoff:
            continue
        yield r, ts


def report(window: timedelta) -> dict:
    cutoff = datetime.now(timezone.utc) - window
    per_pattern: Counter[str] = Counter()
    per_bucket: Counter[str] = Counter()
    latest_ts: dict[str, datetime] = {}
    pattern_to_bucket: dict[str, str] = {}

    if not QUEUE_ROOT.is_dir():
        return {"error": f"no queue at {QUEUE_ROOT}"}

    for jsonl in sorted(QUEUE_ROOT.glob("*.jsonl")):
        bucket = jsonl.stem
        for rec, ts in _walk_queue(jsonl, cutoff):
            pid = rec.get("pattern_id") or rec.get("kind") or "(none)"
            per_pattern[pid] += 1
            per_bucket[bucket] += 1
            pattern_to_bucket[pid] = bucket
            if ts is not None:
                if pid not in latest_ts or ts > latest_ts[pid]:
                    latest_ts[pid] = ts

    return {
        "window_hours": window.total_seconds() / 3600,
        "cutoff_utc": cutoff.isoformat(),
        "by_pattern": dict(per_pattern),
        "by_bucket": dict(per_bucket),
        "pattern_to_bucket": pattern_to_bucket,
        "latest_ts": {k: v.isoformat() for k, v in latest_ts.items()},
    }


def _format_text(rep: dict) -> str:
    if "error" in rep:
        return f"ERROR: {rep['error']}"
    lines = []
    lines.append(
        f"dispatch report — last {rep['window_hours']:.1f}h "
        f"(cutoff {rep['cutoff_utc']})"
    )
    lines.append("")
    total = sum(rep["by_pattern"].values())
    lines.append(f"total signals in window: {total}")
    lines.append("")
    lines.append("by bucket:")
    for b, n in sorted(rep["by_bucket"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {b:<24s}  {n:>6d}")
    lines.append("")
    lines.append(f"{'pattern':<45s}  {'bucket':<14s}  {'count':>6s}  latest")
    for pid, n in sorted(rep["by_pattern"].items(), key=lambda kv: -kv[1])[:25]:
        bucket = rep["pattern_to_bucket"].get(pid, "?")
        ts = rep["latest_ts"].get(pid, "")
        lines.append(f"  {pid[:45]:<45s}  {bucket:<14s}  {n:>6d}  {ts[:19]}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", type=str, default="12h",
                    help="Trailing window. Format: NUM<s|m|h|d|w>.")
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON instead of text.")
    args = ap.parse_args()

    try:
        window = _parse_window(args.window)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    rep = report(window)
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        print(_format_text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())

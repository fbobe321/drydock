#!/usr/bin/env python3
"""queue_top.py — operator-friendly view of the dispatch queues.

Reads `~/.drydock/dispatch/<bucket>.jsonl` files and prints:

- Per-bucket totals + how many of each pattern_id
- Most recent N records per bucket
- Patterns that have NOT been addressed by a commit in the last 24h
  (potential autonomous_review backlog)

Designed to be run interactively or via `bash queue_top.py` from a
terminal. No flags needed for the common case; --help for options.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

QUEUE_ROOT = Path.home() / ".drydock" / "dispatch"


def load_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def patterns_in_recent_commits(hours: int = 24) -> set[str]:
    """Find pattern_ids mentioned in commit messages over the last N hours.

    Looks for the convention `addresses pattern <id>:` introduced in
    autonomous_review's prompt update. Treats it as the dedup signal.
    """
    try:
        result = subprocess.run(
            [
                "git", "log",
                f"--since={hours} hours ago",
                "--pretty=format:%s%n%b",
            ],
            cwd="/data3/drydock",
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return set()
    text = result.stdout
    found: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        # match "addresses pattern <id>" anywhere in the line
        marker = "addresses pattern "
        idx = line.lower().find(marker)
        if idx >= 0:
            tail = line[idx + len(marker):].strip()
            # Pattern ID ends at whitespace, colon, or end of line
            for sep in (":", " ", ","):
                if sep in tail:
                    tail = tail.split(sep, 1)[0]
                    break
            if tail:
                found.add(tail.strip())
    return found


def fmt_age(ts_str: str) -> str:
    try:
        ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return "?"
    delta = datetime.now(timezone.utc) - ts
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def report(args: argparse.Namespace) -> int:
    queue_dir = Path(args.queue_root) if args.queue_root else QUEUE_ROOT
    if not queue_dir.is_dir():
        print(f"No dispatch queue directory: {queue_dir}")
        return 1

    queues = sorted(queue_dir.glob("*.jsonl"))
    if not queues:
        print(f"No queue files in {queue_dir}.")
        return 0

    addressed = patterns_in_recent_commits(hours=args.lookback_hours)
    if addressed:
        print(f"Patterns addressed in commits (last {args.lookback_hours}h): "
              f"{len(addressed)}")
        for p in sorted(addressed):
            print(f"  - {p}")
        print()

    for q in queues:
        records = load_records(q)
        if not records:
            continue
        bucket_name = q.stem
        print(f"=== {bucket_name} ({len(records)} records) ===")

        # By pattern_id
        counter = Counter(r.get("pattern_id", "?") for r in records)
        print("  Top patterns:")
        for pat, count in counter.most_common(10):
            mark = "✓" if pat in addressed else " "
            print(f"    [{mark}] {count:4d}  {pat}")

        # Most recent N
        recent = sorted(
            records, key=lambda r: r.get("ts", ""), reverse=True
        )[: args.recent]
        print(f"  {len(recent)} most recent:")
        for r in recent:
            ts = r.get("ts", "?")
            age = fmt_age(ts)
            pat = r.get("pattern_id", "?")
            ev = (r.get("evidence", "") or "")[:80]
            print(f"    [{age:>4s}]  {pat:50s}  {ev}")

        # Pending = patterns with records but not in `addressed`
        pending_patterns = [
            (pat, count)
            for pat, count in counter.most_common(20)
            if pat not in addressed
        ]
        if pending_patterns:
            print(f"  Pending (NOT in last-{args.lookback_hours}h commits):")
            for pat, count in pending_patterns[:5]:
                last = next(
                    (r for r in recent if r.get("pattern_id") == pat),
                    None,
                )
                action = (last or {}).get("suggested_action", "")
                print(f"    {count:4d}  {pat}")
                if action:
                    print(f"           → {action}")
        print()

    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Show dispatch queue contents grouped by bucket + pattern."
    )
    p.add_argument(
        "--queue-root",
        default="",
        help=f"Override queue dir (default: {QUEUE_ROOT})",
    )
    p.add_argument(
        "--recent",
        type=int,
        default=5,
        help="How many recent records to show per bucket (default 5)",
    )
    p.add_argument(
        "--lookback-hours",
        type=int,
        default=24,
        help="Window for the 'addressed in recent commits' dedup (default 24h)",
    )
    args = p.parse_args()
    return report(args)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Consume the RETRIEVAL bucket from the classifier dispatch queue.

For each fresh entry in ~/.drydock/dispatch/retrieval.jsonl:
  - parse the evidence-row timestamp
  - find the session whose start_time is within +/-1h of it
  - read the session's working_directory
  - if that project hasn't been ingested into GraphRAG in the last 7 days,
    run `python -m drydock.graphrag ingest <project>`

State is tracked in ~/.drydock/dispatch/.retrieval_consumed.json so reruns
are idempotent and cheap.

This is the closing-the-loop step the framework was missing: the classifier
flagged 12+ retrieval-pattern hits but no consumer was actually ingesting
the affected projects' source into GraphRAG.

Usage:
    python3 scripts/consume_retrieval_queue.py            # dry run
    python3 scripts/consume_retrieval_queue.py --apply    # do the ingest
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DISPATCH_DIR = Path.home() / ".drydock" / "dispatch"
QUEUE = DISPATCH_DIR / "retrieval.jsonl"
STATE = DISPATCH_DIR / ".retrieval_consumed.json"
SESSION_ROOT = Path.home() / ".vibe" / "logs" / "session"

REINGEST_AFTER_DAYS = 7
SESSION_MATCH_WINDOW = timedelta(hours=1)
EVIDENCE_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z))")


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _load_state() -> dict[str, str]:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(state: dict[str, str]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, sort_keys=True))


def _evidence_timestamp(evidence: str) -> datetime | None:
    m = EVIDENCE_TS.match(evidence or "")
    return _parse_iso(m.group(1)) if m else None


def _read_queue() -> list[dict]:
    if not QUEUE.exists():
        return []
    out: list[dict] = []
    for line in QUEUE.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


_DIRNAME_TS = re.compile(r"^session_(\d{8})_(\d{6})_")


def _dirname_to_dt(name: str) -> datetime | None:
    """Parse a session directory name into a UTC datetime without reading
    meta.json. Drydock encodes start time in the dir name as
    `session_YYYYMMDD_HHMMSS_<id>`."""
    m = _DIRNAME_TS.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2),
                                 "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _find_session_cwd(target: datetime) -> Path | None:
    """Find the session whose start_time is closest to `target` and within
    SESSION_MATCH_WINDOW.

    Two-stage filter: we used to read every meta.json under SESSION_ROOT
    (10K+ files, ~40s wall) which made autonomous_review's per-bash
    timeout abandon this consumer every tick. Now we filter on the
    directory-name timestamp first (zero I/O), then only read meta.json
    for the handful of candidates within the time window.
    """
    if not SESSION_ROOT.exists():
        return None
    window_s = SESSION_MATCH_WINDOW.total_seconds()
    candidates: list[tuple[float, Path]] = []
    for entry in SESSION_ROOT.iterdir():
        if not entry.is_dir():
            continue
        dt = _dirname_to_dt(entry.name)
        if dt is None:
            continue
        if abs((dt - target).total_seconds()) > window_s:
            continue
        meta = entry / "meta.json"
        try:
            data = json.loads(meta.read_text())
        except Exception:
            continue
        start = _parse_iso(data.get("start_time", "")) or dt
        delta = abs((start - target).total_seconds())
        if delta > window_s:
            continue
        cwd = (data.get("environment") or {}).get("working_directory")
        if cwd:
            candidates.append((delta, Path(cwd)))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def _stale(state: dict[str, str], project: Path) -> bool:
    last = _parse_iso(state.get(str(project), ""))
    if last is None:
        return True
    return datetime.now(timezone.utc) - last > timedelta(days=REINGEST_AFTER_DAYS)


def _ingest(project: Path) -> tuple[int, str]:
    cmd = [sys.executable, "-m", "drydock.graphrag", "ingest", str(project)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually run graphrag ingest (default is dry-run)")
    ap.add_argument("--limit", type=int, default=20,
                    help="max projects to ingest per run")
    args = ap.parse_args()

    entries = _read_queue()
    if not entries:
        print("[retrieval-consumer] queue empty, nothing to do")
        return 0

    state = _load_state()
    targets: dict[str, datetime] = {}
    skipped_no_session = 0
    skipped_no_ts = 0

    for e in entries:
        ts = _evidence_timestamp(e.get("evidence", ""))
        if ts is None:
            skipped_no_ts += 1
            continue
        cwd = _find_session_cwd(ts)
        if cwd is None:
            skipped_no_session += 1
            continue
        if not cwd.exists():
            continue
        key = str(cwd)
        if not _stale(state, cwd):
            continue
        prior = targets.get(key)
        if prior is None or ts > prior:
            targets[key] = ts

    if not targets:
        print(f"[retrieval-consumer] {len(entries)} queue entries, "
              f"0 actionable (skipped: no_ts={skipped_no_ts}, "
              f"no_session={skipped_no_session}, all already ingested recently)")
        return 0

    items = list(targets.items())[: args.limit]
    print(f"[retrieval-consumer] {len(entries)} queue entries → "
          f"{len(items)} project(s) to ingest"
          f"{' (DRY RUN — pass --apply to execute)' if not args.apply else ''}")

    for project, ts in items:
        print(f"  {project}  (evidence ts {ts.isoformat()})")
        if not args.apply:
            continue
        rc, out = _ingest(Path(project))
        snippet = (out[-300:] if out else "").replace("\n", " | ")
        print(f"    rc={rc} {snippet}")
        if rc == 0:
            state[project] = datetime.now(timezone.utc).isoformat()
            _save_state(state)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

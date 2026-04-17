"""Plain-text audit log for every intervention Admiral applies.

Per the PRD: "Every change Admiral makes to Drydock's state must be
written to a plain-text admiral_history.log." Kept simple on purpose —
no JSON, no rotation logic, just append-only timestamped lines.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def _log_path() -> Path:
    d = Path.home() / ".drydock" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "admiral_history.log"


def append(event: str, detail: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"{ts} | {event} | {detail}\n"
    with _log_path().open("a") as f:
        f.write(line)

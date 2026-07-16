"""Durable session snapshots for task resume (PRD Epic P / P2.1).

The event trace records WHAT happened, but not the live message transcript, so it
can't by itself continue an interrupted task. This writes a compact, atomic
snapshot of the session — the transcript plus the structured task state and
budget — after each turn, so a crash (or a killed process, or a closed laptop)
can be picked up where it left off. The snapshot is cleared on clean completion,
so a leftover file always means "this session was interrupted".

Atomic (write-temp-then-rename) and fully defensive: a snapshot failure must
never break a run. All logic original to Drydock.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def snapshot_dir() -> Path:
    return Path.home() / ".drydock" / "resume"


def snapshot_path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (session_id or "session"))
    return snapshot_dir() / f"{safe}.json"


def save_snapshot(state, config: dict, path) -> Path | None:
    """Atomically write a resume snapshot for `state`. Returns the path, or None on
    failure (never raises). Call at a SAFE point (tool results all appended) so the
    transcript is consistent."""
    path = Path(path)
    try:
        snap = {
            "session_id": config.get("session_id", path.stem),
            "ts": round(time.time(), 3),
            "model": config.get("model"),
            "provider": config.get("provider"),
            "base_url": config.get("base_url"),
            "cwd": config.get("cwd"),
            "turn_count": getattr(state, "turn_count", 0),
            "task": state.task.to_dict() if getattr(state, "task", None) else {},
            "budget": state.budget.to_dict() if getattr(state, "budget", None) else {},
            "messages": state.messages,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snap, default=str, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)  # atomic on the same filesystem
        return path
    except (OSError, TypeError, ValueError):
        return None


def load_snapshot(path) -> dict | None:
    """Read a snapshot dict, or None if missing/unreadable."""
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_snapshot(path) -> None:
    """Remove a snapshot (on clean completion). Never raises."""
    try:
        Path(path).unlink()
    except OSError:
        pass


def list_snapshots() -> list[Path]:
    """Resumable sessions, most recent first."""
    try:
        snaps = [p for p in snapshot_dir().glob("*.json") if p.is_file()]
    except OSError:
        return []
    return sorted(snaps, key=lambda p: p.stat().st_mtime, reverse=True)


def latest_snapshot() -> Path | None:
    snaps = list_snapshots()
    return snaps[0] if snaps else None

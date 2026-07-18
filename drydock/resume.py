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
from dataclasses import dataclass, field
from pathlib import Path

# Effects that must NOT be blindly retried on resume (PRD P2.3): an interrupted
# external mutation may or may not have taken effect on the far side.
_UNSAFE_TO_RETRY = frozenset({"external_mutation", "credential_access", "destructive"})


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
            "events_path": str(state.events.path) if getattr(state, "events", None) else "",
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


# ── Reconstruction ────────────────────────────────────────────────────────

@dataclass
class ResumeInfo:
    """Everything needed to continue an interrupted task (PRD P2.1/P2.2)."""
    session_id: str = ""
    model: str | None = None
    provider: str | None = None
    base_url: str | None = None
    cwd: str | None = None
    turn_count: int = 0
    task: dict = field(default_factory=dict)      # TaskState.to_dict()
    budget: dict = field(default_factory=dict)
    messages: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)  # in-flight tool_started dicts
    warnings: list = field(default_factory=list)

    @property
    def objective(self) -> str:
        return self.task.get("objective", "")

    @property
    def has_unsafe_unresolved(self) -> bool:
        return any(a.get("effect") in _UNSAFE_TO_RETRY for a in self.unresolved)


def restore(snapshot_path, event_trace=None) -> ResumeInfo | None:
    """Build a ResumeInfo from a session snapshot, cross-referencing the event
    trace for in-flight (unresolved) actions. Returns None if the snapshot is
    missing/unreadable. Classifies unresolved external mutations as unsafe to
    retry (P2.3) and records a warning."""
    snap = load_snapshot(snapshot_path)
    if snap is None:
        return None
    info = ResumeInfo(
        session_id=snap.get("session_id", ""),
        model=snap.get("model"), provider=snap.get("provider"),
        base_url=snap.get("base_url"), cwd=snap.get("cwd"),
        turn_count=snap.get("turn_count", 0),
        task=snap.get("task") or {}, budget=snap.get("budget") or {},
        messages=snap.get("messages") or [],
    )
    # Fall back to the event trace recorded in the snapshot itself.
    if event_trace is None:
        et = snap.get("events_path")
        if et and Path(et).exists():
            event_trace = et
    if event_trace is not None:
        from drydock.events import find_unresolved
        info.unresolved = find_unresolved(event_trace)
    for a in info.unresolved:
        eff = a.get("effect")
        if eff in _UNSAFE_TO_RETRY:
            info.warnings.append(
                f"{a.get('name')} ({eff}) was in-flight when interrupted — it may "
                f"have taken effect; verify before retrying.")
        else:
            info.warnings.append(
                f"{a.get('name')} was interrupted mid-call; it's read-only/local, "
                f"safe to redo.")
    return info


def resume_note(info: ResumeInfo) -> str:
    """A message to inject into the resumed transcript so the model knows it's
    continuing an interrupted task and re-checks anything left in-flight."""
    lines = ["[RESUMING an interrupted task. The objective and history above are "
             "restored — continue from where it left off; do not restart.]"]
    if info.unresolved:
        lines.append("When it was interrupted, these actions were IN-FLIGHT and may "
                     "not have finished — check their effect before repeating them:")
        for a in info.unresolved:
            lines.append(f"  - {a.get('name')} ({a.get('effect', 'unknown')})")
    return "\n".join(lines)


def apply_to_state(info: ResumeInfo, state) -> None:
    """Populate an AgentState from a ResumeInfo: restore the transcript, structured
    task state and cumulative budget so the run continues seamlessly."""
    from drydock.task_state import TaskState
    state.messages = list(info.messages)
    state.task = TaskState.from_dict(info.task) if info.task else state.task
    state.turn_count = info.turn_count
    if getattr(state, "budget", None) is not None:
        state.budget.session_turns = info.budget.get("session_turns", 0)
        state.budget.session_tool_calls = info.budget.get("session_tool_calls", 0)

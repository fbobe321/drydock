"""Durable execution event log (Agent-Buildout PRD, Epic: events).

Every task run appends structured events to a per-session JSONL file so a task can
be inspected, diagnosed, replayed, and later resumed. Append-only, one event per
line: ``{"seq", "ts", "type", ...}``. DEFENSIVE — a logging failure must never crash
the agent, so every I/O path swallows its own errors. All logic original to Drydock.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class EventLog:
    """Append-only JSONL sink for execution events. Cheap to call; never raises."""

    def __init__(self, path):
        self.path = Path(path)
        self._seq = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def emit(self, type: str, **data) -> None:
        self._seq += 1
        rec = {"seq": self._seq, "ts": round(time.time(), 3), "type": type}
        rec.update(data)
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError):
            pass  # logging must never break a run

    @staticmethod
    def read(path) -> list[dict]:
        """Parse an event-log JSONL into a list of event dicts (in order). Skips any
        unparseable line; returns [] if the file is missing."""
        out: list[dict] = []
        try:
            text = Path(path).read_text("utf-8", errors="ignore")
        except OSError:
            return out
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


def emit(state, type: str, **data) -> None:
    """Emit to the run's event log if it has one — a no-op otherwise. Lets the agent
    loop log unconditionally without a log everywhere."""
    log = getattr(state, "events", None)
    if log is not None:
        log.emit(type, **data)


def default_event_log_path():
    """A fresh per-session JSONL path under ~/.drydock/events/."""
    import time
    from pathlib import Path
    return Path.home() / ".drydock" / "events" / f"session-{int(time.time())}.jsonl"


def _events(events_or_path):
    return events_or_path if isinstance(events_or_path, list) else EventLog.read(events_or_path)


def reconstruct_task_state(events_or_path):
    """Rebuild a TaskState (objective, acceptance criteria, phase) from an event
    trace — the PRD "a task can be reconstructed from serialized state" / resume.
    Returns a fresh TaskState (empty if the trace has no task_start)."""
    from drydock.task_state import TaskState
    ts = TaskState()
    for e in _events(events_or_path):
        t = e.get("type")
        if t == "task_start":
            ts.objective = e.get("objective", "") or ts.objective
            ts.acceptance_criteria = list(e.get("acceptance_criteria", []) or ts.acceptance_criteria)
        elif t in ("verify_gate", "verification", "done") and e.get("phase"):
            ts.phase = e.get("phase", ts.phase)
        elif t == "verify_gate":
            ts.phase = "repair" if e.get("kind") == "failed" else "verify"
        elif t == "done":
            ts.phase = e.get("phase", ts.phase)
    return ts


def summarize(events_or_path) -> dict:
    """A compact digest of a task's execution trace for inspection/diagnosis."""
    evs = _events(events_or_path)
    tools: dict[str, int] = {}
    turns = in_tok = out_tok = verify_pass = verify_fail = 0
    phase = "understand"
    for e in evs:
        t = e.get("type")
        if t == "turn":
            turns += 1; in_tok += e.get("in_tok", 0) or 0; out_tok += e.get("out_tok", 0) or 0
        elif t == "tool":
            tools[e.get("name", "?")] = tools.get(e.get("name", "?"), 0) + 1
        elif t == "verification":
            if e.get("status") == "pass": verify_pass += 1
            elif e.get("status") == "fail": verify_fail += 1
        elif t == "done":
            phase = e.get("phase", phase)
    ts = reconstruct_task_state(evs)
    return {
        "objective": ts.objective, "acceptance_criteria": ts.acceptance_criteria,
        "final_phase": phase, "turns": turns, "tools": tools,
        "in_tok": in_tok, "out_tok": out_tok,
        "verifications": {"pass": verify_pass, "fail": verify_fail},
        "event_count": len(evs),
    }

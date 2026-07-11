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

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


class SQLiteEventLog:
    """Append-only SQLite sink for execution events — same interface as EventLog,
    but backed by an indexed table so a long/large trace can be queried by seq or
    type without scanning a growing JSONL (the same scaling move GraphRAG made).
    Append-only (INSERT only, never UPDATE/DELETE); never raises."""

    def __init__(self, path):
        import sqlite3

        self.path = Path(path)
        self._seq = 0
        self._conn = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "seq INTEGER PRIMARY KEY, ts REAL, type TEXT, data TEXT)"
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")
            self._conn.commit()
            # continue seq past any existing rows (resume-friendly)
            row = self._conn.execute("SELECT MAX(seq) FROM events").fetchone()
            self._seq = row[0] or 0
        except Exception:  # noqa: BLE001 — logging must never break a run
            self._conn = None

    def emit(self, type: str, **data) -> None:
        if self._conn is None:
            return
        self._seq += 1
        try:
            payload = json.dumps(data, default=str, ensure_ascii=False)
            self._conn.execute(
                "INSERT INTO events (seq, ts, type, data) VALUES (?, ?, ?, ?)",
                (self._seq, round(time.time(), 3), type, payload),
            )
            self._conn.commit()
        except Exception:  # noqa: BLE001
            pass  # never break a run on a logging failure

    @staticmethod
    def read(path) -> list[dict]:
        """Return every event as a dict (seq/ts/type + the flattened data), ordered
        by seq. Missing/unreadable db -> []."""
        import sqlite3

        out: list[dict] = []
        try:
            conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
        except sqlite3.Error:
            return out
        try:
            rows = conn.execute("SELECT seq, ts, type, data FROM events ORDER BY seq").fetchall()
        except sqlite3.Error:
            conn.close()
            return out
        conn.close()
        for seq, ts, type_, data in rows:
            rec = {"seq": seq, "ts": ts, "type": type_}
            try:
                rec.update(json.loads(data) if data else {})
            except (json.JSONDecodeError, TypeError):
                pass
            out.append(rec)
        return out


def format_timeline(events_or_path, limit: int = 40) -> list[str]:
    """A compact one-line-per-event timeline for operator inspection (the /trace
    view). Returns the most recent `limit` events, oldest-first, each rendered to a
    short string highlighting what happened — especially governor activity."""
    evs = _events(events_or_path)
    shown = evs[-limit:] if limit and len(evs) > limit else evs
    out: list[str] = []
    if limit and len(evs) > limit:
        out.append(f"… {len(evs) - limit} earlier events omitted …")
    for e in shown:
        t = e.get("type")
        seq = e.get("seq", "?")
        if t == "task_start":
            detail = f"objective: {(e.get('objective') or '')[:60]}"
        elif t == "turn":
            detail = f"turn  (+{e.get('in_tok', 0)}/{e.get('out_tok', 0)} tok)"
        elif t == "tool_started":
            detail = f"▶ {e.get('name')}  ({e.get('effect', '?')})"
        elif t == "tool":
            detail = f"✓ {e.get('name', '?')}  → {e.get('status', 'ok')}"
        elif t == "verification":
            detail = f"verify: {e.get('status', '?')}"
        elif t == "progress":
            detail = f"progress {e.get('score', 0):+d}  (streak {e.get('streak', 0)})"
        elif t == "recovery":
            extra = " SUPPRESS" if e.get("suppressed") else ""
            extra += " STOP" if e.get("terminate") else ""
            detail = f"⚠ recovery stage {e.get('stage', '?')}{extra}"
        elif t == "plan":
            detail = f"plan v{e.get('version', '?')}"
        elif t == "verify_gate":
            detail = f"gate: {e.get('kind', '?')}"
        elif t == "done":
            detail = f"done  (phase {e.get('phase', '?')})"
        else:
            detail = t or "?"
        out.append(f"  {seq:>3}  {detail}")
    return out


def read_events(path) -> list[dict]:
    """Read an event trace regardless of backend — SQLite for .db/.sqlite paths,
    JSONL otherwise. Lets summarize()/reconstruct_task_state() work with either."""
    s = str(path)
    if s.endswith((".db", ".sqlite", ".sqlite3")):
        return SQLiteEventLog.read(path)
    return EventLog.read(path)


def make_event_log(path, backend: str = "jsonl"):
    """Construct the configured event-log backend for `path`. backend 'sqlite'
    (or a .db/.sqlite path) -> SQLiteEventLog, else JSONL EventLog."""
    s = str(path)
    if backend == "sqlite" or s.endswith((".db", ".sqlite", ".sqlite3")):
        return SQLiteEventLog(path)
    return EventLog(path)


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
    return events_or_path if isinstance(events_or_path, list) else read_events(events_or_path)


def find_unresolved(events_or_path) -> list[dict]:
    """In-flight actions the process never finished (PRD P2.2): a `tool_started`
    with no following `tool` (completed) event. Returns the started-event dicts,
    in order. Pairing is by order — execution is single-threaded within a turn, so
    a started is always immediately followed by its completion unless interrupted.

    Each returned dict carries `effect` so the caller can refuse to blindly retry
    an external mutation (P2.3): a read-only in-flight call is safe to redo, a
    mutation is not."""
    pending: list[dict] = []
    for e in _events(events_or_path):
        t = e.get("type")
        if t == "tool_started":
            pending.append(e)
        elif t == "tool" and pending:
            pending.pop()  # this completion clears the most recent started
    return pending


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
    # Governor metrics (Epics L/K): how hard the harness had to work to stay on
    # track — the recovery stage it reached and how often it intervened, plus the
    # worst no-progress streak. Inspection only (not an eval harness).
    max_recovery_stage = recovery_interventions = suppressions = max_streak = 0
    for e in evs:
        t = e.get("type")
        if t == "turn":
            turns += 1; in_tok += e.get("in_tok", 0) or 0; out_tok += e.get("out_tok", 0) or 0
        elif t == "tool":
            tools[e.get("name", "?")] = tools.get(e.get("name", "?"), 0) + 1
        elif t == "verification":
            if e.get("status") == "pass": verify_pass += 1
            elif e.get("status") == "fail": verify_fail += 1
        elif t == "progress":
            max_streak = max(max_streak, e.get("streak", 0) or 0)
        elif t == "recovery":
            recovery_interventions += 1
            max_recovery_stage = max(max_recovery_stage, e.get("stage", 0) or 0)
            if e.get("suppressed"): suppressions += 1
        elif t == "done":
            phase = e.get("phase", phase)
    ts = reconstruct_task_state(evs)
    return {
        "objective": ts.objective, "acceptance_criteria": ts.acceptance_criteria,
        "final_phase": phase, "turns": turns, "tools": tools,
        "in_tok": in_tok, "out_tok": out_tok,
        "verifications": {"pass": verify_pass, "fail": verify_fail},
        "recovery": {"max_stage": max_recovery_stage, "interventions": recovery_interventions,
                     "suppressions": suppressions, "max_no_progress_streak": max_streak},
        "event_count": len(evs),
    }

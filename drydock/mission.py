"""Long-horizon autonomous missions — durable state layer.

See docs/mission_prd.md. The architectural principle (§50/§52): *missions* run for
hours/days, *agents* are disposable workers doing bounded units against durable state — "do
not make the agent's loop longer, make the work durable." So the mission's objective, tasks,
experiments, knowledge, checkpoints, budgets, and event log all live OUTSIDE any model
context, in SQLite (§11), and survive worker crashes and Drydock restarts (§28, G1).

This module is the state layer (Phase 1 foundation): schema + `MissionStore`, a transactional,
model-neutral (§33) CRUD/queue API. Higher layers (planner, worker, evaluator, controller,
CLI) build on it. Stdlib-only (sqlite3); logging-style writes never raise.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

# ── mission lifecycle (§8) ────────────────────────────────────────────────────
M_CREATED = "CREATED"
M_INITIALIZING = "INITIALIZING"
M_BASELINING = "BASELINING"
M_PLANNING = "PLANNING"
M_EXECUTING = "EXECUTING"
M_EVALUATING = "EVALUATING"
M_STRATEGIC_REVIEW = "STRATEGIC_REVIEW"
M_COMPLETED = "COMPLETED"
M_PAUSED = "PAUSED"
M_BLOCKED = "BLOCKED"
M_FAILED = "FAILED"
M_CANCELLED = "CANCELLED"
M_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
M_AWAITING_HUMAN = "AWAITING_HUMAN"
_TERMINAL = {M_COMPLETED, M_FAILED, M_CANCELLED, M_BUDGET_EXHAUSTED}

# ── task lifecycle (§25) ──────────────────────────────────────────────────────
T_PENDING = "PENDING"
T_READY = "READY"
T_LEASED = "LEASED"
T_RUNNING = "RUNNING"
T_EVALUATING = "EVALUATING"
T_COMPLETED = "COMPLETED"
T_FAILED = "FAILED"
T_BLOCKED = "BLOCKED"
T_CANCELLED = "CANCELLED"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
    id TEXT PRIMARY KEY, objective TEXT NOT NULL, success_criteria TEXT, status TEXT,
    budget TEXT, config TEXT, baseline TEXT, current_metric REAL, best_metric REAL,
    created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, mission_id TEXT, milestone TEXT, objective TEXT, reason TEXT,
    status TEXT, priority INTEGER, deps TEXT, inputs TEXT, constraints TEXT,
    allowed_tools TEXT, success_criteria TEXT, assignee TEXT, lease_expires REAL,
    attempts INTEGER DEFAULT 0, result TEXT, created_at REAL, updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_mission ON tasks(mission_id, status);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT, ts REAL, type TEXT, data TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_mission ON events(mission_id, seq);
CREATE TABLE IF NOT EXISTS usage (
    mission_id TEXT PRIMARY KEY, tokens INTEGER DEFAULT 0, wall_s REAL DEFAULT 0,
    experiments INTEGER DEFAULT 0, failures INTEGER DEFAULT 0
);
"""


def _now() -> float:
    return time.time()


def _j(v) -> str:
    try:
        return json.dumps(v, default=str)
    except (TypeError, ValueError):
        return "null"


def _u(s) -> object:
    try:
        return json.loads(s) if s else None
    except (TypeError, ValueError):
        return None


class MissionStore:
    """SQLite-backed durable mission state. Transactional for every state change (§11);
    the event log is append-only and immutable (§12). Model-neutral — a mission started by
    one model resumes under another (§33)."""

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # ── missions ──────────────────────────────────────────────────────────────
    def create_mission(self, objective: str, *, success_criteria: dict | None = None,
                        budget: dict | None = None, config: dict | None = None,
                        mission_id: str | None = None) -> str:
        mid = mission_id or f"mission-{uuid.uuid4().hex[:8]}"
        now = _now()
        with self.conn:
            self.conn.execute(
                "INSERT INTO missions(id,objective,success_criteria,status,budget,config,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (mid, objective, _j(success_criteria or {}), M_CREATED, _j(budget or {}),
                 _j(config or {}), now, now))
            self.conn.execute("INSERT OR IGNORE INTO usage(mission_id) VALUES(?)", (mid,))
        self.event(mid, "mission_created", objective=objective,
                   success_criteria=success_criteria or {}, budget=budget or {})
        return mid

    def get_mission(self, mission_id: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
        if r is None:
            return None
        m = dict(r)
        for k in ("success_criteria", "budget", "config", "baseline"):
            m[k] = _u(m.get(k))
        return m

    def list_missions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id,objective,status,current_metric,best_metric,created_at "
            "FROM missions ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def set_status(self, mission_id: str, status: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE missions SET status=?,updated_at=? WHERE id=?",
                              (status, _now(), mission_id))
        self.event(mission_id, "status", status=status)

    def set_baseline(self, mission_id: str, baseline: dict) -> None:
        with self.conn:
            self.conn.execute("UPDATE missions SET baseline=?,updated_at=? WHERE id=?",
                              (_j(baseline), _now(), mission_id))
        self.event(mission_id, "baseline", baseline=baseline)  # immutable in history (§9)

    def set_metric(self, mission_id: str, current: float) -> None:
        """Record the mission's current metric, tracking the best seen (§38)."""
        with self.conn:
            m = self.conn.execute("SELECT best_metric FROM missions WHERE id=?",
                                  (mission_id,)).fetchone()
            best = m["best_metric"] if m and m["best_metric"] is not None else None
            new_best = current if best is None else max(best, current)
            self.conn.execute("UPDATE missions SET current_metric=?,best_metric=?,updated_at=? "
                              "WHERE id=?", (current, new_best, _now(), mission_id))

    # ── tasks / queue (§25) ─────────────────────────────────────────────────────
    def add_task(self, mission_id: str, objective: str, *, reason: str = "", priority: int = 0,
                 deps: list[str] | None = None, milestone: str = "", inputs: dict | None = None,
                 constraints: dict | None = None, allowed_tools: list[str] | None = None,
                 success_criteria: dict | None = None) -> str:
        tid = f"task-{uuid.uuid4().hex[:8]}"
        now = _now()
        status = T_READY if not deps else T_PENDING
        with self.conn:
            self.conn.execute(
                "INSERT INTO tasks(id,mission_id,milestone,objective,reason,status,priority,"
                "deps,inputs,constraints,allowed_tools,success_criteria,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, mission_id, milestone, objective, reason, status, priority, _j(deps or []),
                 _j(inputs or {}), _j(constraints or {}), _j(allowed_tools or []),
                 _j(success_criteria or {}), now, now))
        self.event(mission_id, "task_created", task=tid, objective=objective, priority=priority)
        return tid

    def get_task(self, task_id: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if r is None:
            return None
        t = dict(r)
        for k in ("deps", "inputs", "constraints", "allowed_tools", "success_criteria", "result"):
            t[k] = _u(t.get(k))
        return t

    def tasks(self, mission_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE mission_id=? ORDER BY priority DESC, created_at",
            (mission_id,)).fetchall()
        out = []
        for r in rows:
            t = dict(r)
            for k in ("deps", "inputs", "constraints", "allowed_tools", "success_criteria", "result"):
                t[k] = _u(t.get(k))
            out.append(t)
        return out

    def _completed_ids(self, mission_id: str) -> set[str]:
        return {r["id"] for r in self.conn.execute(
            "SELECT id FROM tasks WHERE mission_id=? AND status=?",
            (mission_id, T_COMPLETED)).fetchall()}

    def promote_ready(self, mission_id: str) -> int:
        """Move PENDING tasks whose deps are all COMPLETED to READY. Returns #promoted."""
        done = self._completed_ids(mission_id)
        n = 0
        with self.conn:
            for r in self.conn.execute(
                    "SELECT id,deps FROM tasks WHERE mission_id=? AND status=?",
                    (mission_id, T_PENDING)).fetchall():
                deps = _u(r["deps"])
                deps = deps if isinstance(deps, list) else []
                if all(d in done for d in deps):
                    self.conn.execute("UPDATE tasks SET status=?,updated_at=? WHERE id=?",
                                      (T_READY, _now(), r["id"]))
                    n += 1
        return n

    def ready_tasks(self, mission_id: str) -> list[dict]:
        self.promote_ready(mission_id)
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE mission_id=? AND status=? "
            "ORDER BY priority DESC, created_at", (mission_id, T_READY)).fetchall()
        return [t for t in (self.get_task(r["id"]) for r in rows) if t is not None]

    def claim_task(self, task_id: str, worker: str, lease_secs: int = 300) -> bool:
        """Atomically lease a READY task (§26). Returns False if it isn't claimable —
        crash-safe and swarm-ready (many workers can race for the same queue)."""
        now = _now()
        with self.conn:
            cur = self.conn.execute(
                "UPDATE tasks SET status=?,assignee=?,lease_expires=?,attempts=attempts+1,"
                "updated_at=? WHERE id=? AND status=?",
                (T_RUNNING, worker, now + lease_secs, now, task_id, T_READY))
            ok = cur.rowcount == 1
        if ok:
            t = self.get_task(task_id)
            if t:
                self.event(t["mission_id"], "task_leased", task=task_id, worker=worker,
                           lease_expires=now + lease_secs)
        return ok

    def heartbeat(self, task_id: str, lease_secs: int = 300) -> None:
        with self.conn:
            self.conn.execute("UPDATE tasks SET lease_expires=?,updated_at=? WHERE id=?",
                              (_now() + lease_secs, _now(), task_id))

    def reclaim_expired(self, mission_id: str) -> int:
        """Return RUNNING/LEASED tasks whose lease expired to READY (§26/§28 recovery)."""
        now = _now()
        n = 0
        with self.conn:
            for r in self.conn.execute(
                    "SELECT id FROM tasks WHERE mission_id=? AND status IN (?,?) "
                    "AND lease_expires IS NOT NULL AND lease_expires<?",
                    (mission_id, T_RUNNING, T_LEASED, now)).fetchall():
                self.conn.execute("UPDATE tasks SET status=?,assignee=NULL,lease_expires=NULL,"
                                  "updated_at=? WHERE id=?", (T_READY, now, r["id"]))
                self.event(mission_id, "lease_expired", task=r["id"])
                n += 1
        return n

    def complete_task(self, task_id: str, status: str, result: dict | None = None) -> None:
        with self.conn:
            self.conn.execute("UPDATE tasks SET status=?,result=?,assignee=NULL,"
                              "lease_expires=NULL,updated_at=? WHERE id=?",
                              (status, _j(result or {}), _now(), task_id))
        t = self.get_task(task_id)
        if t:
            self.event(t["mission_id"], "task_done", task=task_id, status=status)
            if status == T_FAILED:
                self.add_usage(t["mission_id"], failures=1)

    # ── event log (§12) — immutable, append-only ───────────────────────────────
    def event(self, mission_id: str, type: str, **data) -> None:
        try:
            with self.conn:
                self.conn.execute("INSERT INTO events(mission_id,ts,type,data) VALUES(?,?,?,?)",
                                  (mission_id, _now(), type, _j(data)))
        except sqlite3.Error:
            pass  # logging must never break the mission

    def events(self, mission_id: str, limit: int = 0) -> list[dict]:
        q = "SELECT seq,ts,type,data FROM events WHERE mission_id=? ORDER BY seq"
        if limit:
            q += f" DESC LIMIT {int(limit)}"
        rows = self.conn.execute(q, (mission_id,)).fetchall()
        out = []
        for r in rows:
            d = _u(r["data"])
            d = d if isinstance(d, dict) else {}
            out.append({"seq": r["seq"], "ts": r["ts"], "type": r["type"], **d})
        return out[::-1] if limit else out

    # ── budgets / usage (§29/§30) ───────────────────────────────────────────────
    def add_usage(self, mission_id: str, *, tokens: int = 0, wall_s: float = 0.0,
                  experiments: int = 0, failures: int = 0) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE usage SET tokens=tokens+?,wall_s=wall_s+?,experiments=experiments+?,"
                "failures=failures+? WHERE mission_id=?",
                (tokens, wall_s, experiments, failures, mission_id))

    def usage(self, mission_id: str) -> dict:
        r = self.conn.execute("SELECT * FROM usage WHERE mission_id=?", (mission_id,)).fetchone()
        return dict(r) if r else {"tokens": 0, "wall_s": 0.0, "experiments": 0, "failures": 0}

    def budget_exhausted(self, mission_id: str) -> tuple[bool, str]:
        """True + reason if any finite budget is spent (§30). Wall-clock is measured from
        mission creation; the others from recorded usage."""
        m = self.get_mission(mission_id)
        if not m:
            return False, ""
        b = m.get("budget") or {}
        u = self.usage(mission_id)
        wall_h = (_now() - (m.get("created_at") or _now())) / 3600.0
        checks = [
            ("wall_time_hours", wall_h, "wall-clock budget"),
            ("token_budget", u["tokens"], "token budget"),
            ("max_experiments", u["experiments"], "experiment budget"),
            ("max_failures", u["failures"], "failure budget"),
        ]
        for key, spent, label in checks:
            cap = b.get(key)
            if cap and spent >= cap:
                return True, f"{label} reached ({spent:.0f} ≥ {cap})"
        return False, ""


# ── workspace + factory (§40) ─────────────────────────────────────────────────
def missions_dir(cwd: str | Path) -> Path:
    return Path(cwd) / ".drydock" / "missions"


def open_store(cwd: str | Path, mission_id: str) -> MissionStore:
    return MissionStore(missions_dir(cwd) / mission_id / "state.db")


def create_mission(cwd: str | Path, objective: str, *, success_criteria: dict | None = None,
                   budget: dict | None = None, config: dict | None = None) -> tuple[str, MissionStore]:
    """Create a mission with its own workspace under <cwd>/.drydock/missions/<id>/ (§40)."""
    mid = f"mission-{uuid.uuid4().hex[:8]}"
    store = MissionStore(missions_dir(cwd) / mid / "state.db")
    store.create_mission(objective, success_criteria=success_criteria, budget=budget,
                         config=config, mission_id=mid)
    return mid, store


def list_missions(cwd: str | Path) -> list[str]:
    try:
        return sorted(p.name for p in missions_dir(cwd).iterdir() if p.is_dir())
    except OSError:
        return []

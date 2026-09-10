"""Multi-agent swarm orchestration for Drydock.

See docs/multi_agent_swarm_prd.md. This is the MVP (PRD §35): a single-machine swarm
that deploys 2-8 in-process worker agents against one objective, isolates each Builder
in its own git worktree (§16), records first-class candidate solutions (§17) on a shared
blackboard (§10), verifies them independently (§18), and converges on the best by
evidence (§20) — never by an agent's self-report.

This file is layered so each piece is testable on its own:
  * Blackboard  — the shared-knowledge store (this section). Append-only JSONL under
                  <cwd>/.drydock/swarms/<id>/, following the events.py / rmf.py idioms:
                  swallow-all-errors I/O, typed accessors over plain dicts, and a
                  unified EventLog (§27) so a run survives interruption (§28).
  * worker      — one agent-in-a-worktree run to a candidate (added next).
  * coordinator — decompose + diversity-inject + fan out + verify + judge (added next).
  * run_cli     — the `drydock swarm` subcommand (added next).

Stdlib-only, provider-agnostic — consistent with the rest of Drydock.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from drydock.events import make_event_log

# ── statuses (PRD §11, §13, §17) ─────────────────────────────────────────────
# Hypothesis lifecycle (§11).
HYP_UNTESTED = "UNTESTED"
HYP_INVESTIGATING = "INVESTIGATING"
HYP_SUPPORTED = "SUPPORTED"
HYP_WEAKENED = "WEAKENED"
HYP_REJECTED = "REJECTED"
HYP_CONFIRMED = "CONFIRMED"

# Candidate lifecycle (§17, §18).
CAND_DRAFT = "DRAFT"
CAND_VERIFYING = "VERIFYING"
CAND_PROMISING = "PROMISING"
CAND_REJECTED = "REJECTED"
CAND_ACCEPTED = "ACCEPTED"

# Task / agent lifecycle (§13).
TASK_CREATED = "CREATED"
TASK_ASSIGNED = "ASSIGNED"
TASK_RUNNING = "RUNNING"
TASK_BLOCKED = "BLOCKED"
TASK_FAILED = "FAILED"
TASK_REDUNDANT = "REDUNDANT"
TASK_COMPLETED = "COMPLETED"
TASK_TERMINATED = "TERMINATED"


# ── records (all first-class swarm objects, §9/§11/§17) ──────────────────────
@dataclass
class Discovery:
    """A high-value finding promoted to the blackboard (§9). Only summaries cross
    between agents — never full transcripts — to preserve independence (§8)."""
    id: str
    agent: str
    task: str
    summary: str
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    ts: float = 0.0


@dataclass
class Hypothesis:
    """A tracked explanation with evidence for and against (§11). Confidence comes
    from evidence, not assertion (§20)."""
    id: str
    statement: str
    status: str = HYP_UNTESTED
    confidence: float = 0.0
    supporting: list[str] = field(default_factory=list)
    contradicting: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    ts: float = 0.0


@dataclass
class Candidate:
    """A candidate implementation (§17). Competes on evidence: test counts, critic
    findings, reviewer score, independent verification — not the builder's confidence."""
    id: str
    agent: str
    summary: str = ""
    hypothesis: str = ""
    worktree: str = ""
    base_ref: str = ""
    commit: str = ""
    files_changed: int = 0
    tests_passed: int = 0
    tests_total: int = 0
    reviewer_score: float = 0.0
    critic_findings: int = 0
    verifications: int = 0
    status: str = CAND_DRAFT
    ts: float = 0.0


@dataclass
class SwarmTask:
    """A unit of work on the dynamic task graph (§6). role is the agent role (§7)."""
    id: str
    role: str
    objective: str
    status: str = TASK_CREATED
    assignee: str = ""
    parent: str = ""
    ts: float = 0.0


# Map each store's file to its record type, so read/append share one code path.
_STORES = {
    "discoveries": Discovery,
    "hypotheses": Hypothesis,
    "candidates": Candidate,
    "tasks": SwarmTask,
}


class Blackboard:
    """The swarm's shared-knowledge store (§10). Append-only JSONL with last-write-wins
    by `id`, so a record can be updated (e.g. a hypothesis status transition, §11) by
    appending a new version — the reader keeps the latest. Concurrency-safe for the
    in-process thread pool (§22): every mutation takes `_lock`. Never raises on I/O,
    matching events.py — a logging failure must not take down the swarm."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "candidates").mkdir(exist_ok=True)
            (self.root / "worktrees").mkdir(exist_ok=True)
        except OSError:
            pass
        self.events = make_event_log(self.root / "events.jsonl")

    # ── event stream (§27) ───────────────────────────────────────────────────
    def emit(self, type: str, **data) -> None:
        """Publish a swarm event to the unified stream. Never raises."""
        self.events.emit(type, **data)

    # ── objective + config + metrics (§5, §21, §28, §33) ─────────────────────
    def set_objective(self, objective: str, config: dict | None = None) -> None:
        self._write_json("objective.json", {"objective": objective, "ts": time.time()})
        if config is not None:
            self._write_json("config.json", config)

    def objective(self) -> str:
        return str(self._read_json("objective.json").get("objective", ""))

    def config(self) -> dict:
        return self._read_json("config.json")

    def set_metrics(self, metrics: dict) -> None:
        self._write_json("metrics.json", metrics)

    def metrics(self) -> dict:
        return self._read_json("metrics.json")

    # ── typed adders (auto-id, auto-timestamp) ───────────────────────────────
    def add_discovery(self, agent: str, task: str, summary: str,
                      confidence: float = 0.0, evidence: list[str] | None = None) -> Discovery:
        d = Discovery(id=self._next_id("d"), agent=agent, task=task, summary=summary,
                      confidence=confidence, evidence=list(evidence or []), ts=time.time())
        self._append("discoveries", d)
        self.emit("DISCOVERY_CREATED", id=d.id, agent=agent, task=task,
                  confidence=confidence, summary=summary)
        return d

    def add_hypothesis(self, statement: str, agent: str = "",
                       status: str = HYP_UNTESTED, confidence: float = 0.0) -> Hypothesis:
        h = Hypothesis(id=self._next_id("h"), statement=statement, status=status,
                       confidence=confidence, agents=[agent] if agent else [], ts=time.time())
        self._append("hypotheses", h)
        self.emit("HYPOTHESIS_CREATED", id=h.id, statement=statement, status=status)
        return h

    def add_candidate(self, agent: str, summary: str = "", hypothesis: str = "",
                      worktree: str = "", base_ref: str = "", commit: str = "",
                      files_changed: int = 0, status: str = CAND_DRAFT) -> Candidate:
        c = Candidate(id=self._next_id("c"), agent=agent, summary=summary,
                      hypothesis=hypothesis, worktree=worktree, base_ref=base_ref,
                      commit=commit, files_changed=files_changed, status=status, ts=time.time())
        self._append("candidates", c)
        self.emit("CANDIDATE_CREATED", id=c.id, agent=agent, commit=commit,
                  files_changed=files_changed, status=status)
        return c

    def add_task(self, role: str, objective: str, status: str = TASK_CREATED,
                 assignee: str = "", parent: str = "") -> SwarmTask:
        t = SwarmTask(id=self._next_id("t"), role=role, objective=objective,
                      status=status, assignee=assignee, parent=parent, ts=time.time())
        self._append("tasks", t)
        self.emit("TASK_CREATED", id=t.id, role=role, objective=objective)
        return t

    # ── updates (append a new version; reader keeps last by id) ───────────────
    def update(self, store: str, id: str, **fields) -> dict | None:
        """Update a record by id: read the latest, apply `fields`, append the new
        version. Returns the merged dict, or None if `id` is unknown. Emits the matching
        lifecycle event for hypotheses/candidates/tasks so §27 stays complete."""
        cur = self._latest(store, id)
        if cur is None:
            return None
        cur.update(fields)
        cur["ts"] = time.time()
        cls = _STORES[store]
        with self._lock:
            self._append_dict(store, cur)
        if store == "hypotheses" and "status" in fields:
            self.emit("HYPOTHESIS_UPDATED", id=id, status=fields["status"])
            if fields["status"] == HYP_REJECTED:
                self.emit("HYPOTHESIS_REJECTED", id=id)
        elif store == "candidates" and "status" in fields:
            self.emit("CANDIDATE_UPDATED", id=id, status=fields["status"])
        elif store == "tasks" and "status" in fields:
            self.emit("TASK_UPDATED", id=id, status=fields["status"])
        _ = cls  # record type documents the schema; dict round-trips through JSONL
        return cur

    # ── typed readers (last-write-wins by id, insertion order preserved) ──────
    def discoveries(self) -> list[Discovery]:
        return [Discovery(**r) for r in self._read_latest("discoveries")]

    def hypotheses(self) -> list[Hypothesis]:
        return [Hypothesis(**r) for r in self._read_latest("hypotheses")]

    def candidates(self) -> list[Candidate]:
        return [Candidate(**r) for r in self._read_latest("candidates")]

    def tasks(self) -> list[SwarmTask]:
        return [SwarmTask(**r) for r in self._read_latest("tasks")]

    # ── internals ─────────────────────────────────────────────────────────────
    def _next_id(self, prefix: str) -> str:
        with self._lock:
            self._counters[prefix] = self._counters.get(prefix, 0) + 1
            return f"{prefix}-{self._counters[prefix]}"

    def _append(self, store: str, rec) -> None:
        with self._lock:
            self._append_dict(store, asdict(rec))

    def _append_dict(self, store: str, rec: dict) -> None:
        path = self.root / f"{store}.jsonl"
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError):
            pass

    def _read_rows(self, store: str) -> list[dict]:
        path = self.root / f"{store}.jsonl"
        rows: list[dict] = []
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            pass
        return rows

    def _read_latest(self, store: str) -> list[dict]:
        """Collapse the append log to the latest row per id, preserving first-seen order."""
        latest: dict[str, dict] = {}
        order: list[str] = []
        for r in self._read_rows(store):
            rid = r.get("id")
            if rid is None:
                continue
            if rid not in latest:
                order.append(rid)
            latest[rid] = r
        return [latest[i] for i in order]

    def _latest(self, store: str, id: str) -> dict | None:
        found = None
        for r in self._read_rows(store):
            if r.get("id") == id:
                found = r
        return dict(found) if found is not None else None

    def _write_json(self, name: str, obj: dict) -> None:
        try:
            (self.root / name).write_text(
                json.dumps(obj, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
        except (OSError, TypeError, ValueError):
            pass

    def _read_json(self, name: str) -> dict:
        try:
            return json.loads((self.root / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


# ── swarm directory factory (§28) ────────────────────────────────────────────
def swarms_dir(cwd: str | Path) -> Path:
    """Per-project swarm root: <cwd>/.drydock/swarms/ (matches rmf.py's .drydock/rmf/)."""
    return Path(cwd) / ".drydock" / "swarms"


def new_swarm_id() -> str:
    return f"swarm-{int(time.time())}"


def create_swarm(cwd: str | Path, objective: str, config: dict | None = None,
                 swarm_id: str | None = None) -> Blackboard:
    """Create a fresh swarm blackboard rooted under <cwd>/.drydock/swarms/<id>/."""
    sid = swarm_id or new_swarm_id()
    bb = Blackboard(swarms_dir(cwd) / sid)
    bb.set_objective(objective, config)
    bb.emit("SWARM_CREATED", swarm_id=sid, objective=objective)
    return bb


def open_swarm(cwd: str | Path, swarm_id: str) -> Blackboard:
    """Re-open an existing swarm for resume/inspection (§28)."""
    return Blackboard(swarms_dir(cwd) / swarm_id)


def list_swarms(cwd: str | Path) -> list[str]:
    root = swarms_dir(cwd)
    try:
        return sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        return []

"""Multi-agent swarm orchestration for Drydock.

See docs/multi_agent_swarm_prd.md. This is the MVP (PRD §35): a single-machine swarm
that deploys 2-8 in-process worker agents against one objective, isolates each Builder
in its own git worktree (§16), records first-class candidate solutions (§17) on a shared
blackboard (§10), verifies them independently (§18), and converges on the best by
evidence (§20) — never by an agent's self-report.

This file is layered so each piece is testable on its own:
  * Blackboard  — the shared-knowledge store. Append-only JSONL under
                  <cwd>/.drydock/swarms/<id>/, following the events.py / rmf.py idioms:
                  swallow-all-errors I/O, typed accessors over plain dicts, and a
                  unified EventLog (§27) so a run survives interruption (§28).
  * run_worker  — one agent-in-a-worktree run to a candidate (§16/§17).
  * run_swarm   — coordinator: decompose + diversity-inject + fan out + verify + judge.
  * run_cli     — the `drydock swarm` subcommand (solve / status / list / resume).

Stdlib-only, provider-agnostic — consistent with the rest of Drydock.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from drydock.events import make_event_log

# Builder tool profile (§7): the writable coding loop, mirroring WORKER_TOOLS in
# drydock/tools/__init__.py. Read-only roles (Explorer/Critic) drop Write/Edit.
BUILDER_TOOLS = ("Read", "Write", "Edit", "Bash", "Glob", "Grep", "ViewImage")
EXPLORER_TOOLS = ("Read", "Glob", "Grep", "Bash", "ViewImage")

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
    # Compute cost of producing this candidate — required to compare swarm vs eratchet at
    # MATCHED compute (a win that just spent more inference is not a win). See §21/§33.
    in_tokens: int = 0
    out_tokens: int = 0
    turns: int = 0
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
                      files_changed: int = 0, status: str = CAND_DRAFT,
                      in_tokens: int = 0, out_tokens: int = 0, turns: int = 0) -> Candidate:
        c = Candidate(id=self._next_id("c"), agent=agent, summary=summary,
                      hypothesis=hypothesis, worktree=worktree, base_ref=base_ref,
                      commit=commit, files_changed=files_changed, status=status,
                      in_tokens=in_tokens, out_tokens=out_tokens, turns=turns, ts=time.time())
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


def latest_swarm(cwd: str | Path) -> str | None:
    ids = list_swarms(cwd)
    return ids[-1] if ids else None


# ── worker: one agent in an isolated git worktree → a candidate (§16, §17) ────
# A "swarm worker" is one in-process `drydock.agent.run` scoped to its own worktree
# (cwd), its own tool allowlist, and a fresh private AgentState/context (§8). Builders
# get an isolated worktree so parallel patches never collide (§16); each result is
# snapshotted as a git commit and recorded as a first-class candidate (§17). The agent
# runner is injectable so the worktree/snapshot/candidate plumbing is testable without a
# live model, mirroring eratchet's injected `runner`.

@dataclass
class RunResult:
    """A worker run's summary plus its compute cost (for matched-compute comparison).
    A runner may return a plain summary string instead; run_worker normalizes either."""
    summary: str
    in_tokens: int = 0
    out_tokens: int = 0
    turns: int = 0


# runner(objective, cwd, base_config, system_prompt, allow, max_turns, max_tool_calls)
#   -> summary string OR RunResult (with compute cost).
AgentRunner = Callable[[str, str, dict, str, "list[str]", int, int], "str | RunResult"]


def _git(args: list[str], cwd: str | Path, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=timeout)


def repo_root(cwd: str | Path) -> str | None:
    """The git top-level for `cwd`, or None if not a repo (worktrees require one, §16)."""
    try:
        r = _git(["rev-parse", "--show-toplevel"], cwd)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


# Git serializes worktree-metadata writes, but concurrent `worktree add` on one repo can
# still race; the coordinator adds N at once, so serialize just the (fast) add/remove.
_WORKTREE_LOCK = threading.Lock()


def add_worktree(repo: str | Path, base_ref: str, path: str | Path) -> bool:
    """Create a detached worktree at `path` from `base_ref` (eratchet pattern)."""
    try:
        with _WORKTREE_LOCK:
            r = _git(["worktree", "add", "--detach", str(path), base_ref], repo)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _rmtree(path: str | Path) -> None:
    shutil.rmtree(str(path), ignore_errors=True)


def remove_worktree(repo: str | Path, path: str | Path) -> None:
    """Tear a worktree down (git bookkeeping + its temp parent); never raises."""
    try:
        _git(["worktree", "remove", "--force", str(path)], repo)
    except (OSError, subprocess.SubprocessError):
        pass
    # `path` is <temp-parent>/wt; drop the temp parent too.
    parent = Path(path).parent
    if parent.name.startswith("swarm-") or str(parent).startswith(tempfile.gettempdir()):
        _rmtree(parent)


def snapshot_worktree(wt: str | Path) -> tuple[str, int]:
    """Commit the worker's changes in its worktree and return (commit_sha, files_changed).
    An empty diff yields ("", 0) — a worker that changed nothing produced no candidate."""
    try:
        changed = _git(["status", "--porcelain"], wt).stdout.strip()
        if not changed:
            return "", 0
        n = len([ln for ln in changed.splitlines() if ln.strip()])
        _git(["add", "-A"], wt)
        # -c user.* keeps the snapshot working even where git identity is unset.
        c = _git(["-c", "user.name=drydock-swarm", "-c", "user.email=swarm@drydock",
                  "commit", "-m", "swarm candidate snapshot", "--no-verify"], wt)
        if c.returncode != 0:
            return "", n
        sha = _git(["rev-parse", "HEAD"], wt).stdout.strip()
        return sha, n
    except (OSError, subprocess.SubprocessError):
        return "", 0


def default_agent_runner(objective: str, cwd: str, base_config: dict, system_prompt: str,
                         allow: list[str], max_turns: int, max_tool_calls: int) -> RunResult:
    """Run one in-process agent to completion in `cwd` and return its final summary.

    This is the Dispatch/_run_subagent recipe (drydock/tools/__init__.py): a fresh
    AgentState + a scoped config copy with its own cwd, tool allowlist, and a FRESH
    `_abort` dict — the last is load-bearing for parallel safety, since the provider
    shares `_abort['client']` and a shared holder would let one worker's stop close
    another's in-flight client."""
    from drydock.agent import AgentState, TurnDone
    from drydock.agent import run as agent_run

    state = AgentState()
    cfg = dict(base_config)
    cfg["cwd"] = cwd
    cfg["tool_allowlist"] = list(allow)
    cfg["max_turns"] = max_turns
    cfg["max_tool_calls"] = max_tool_calls
    cfg["trajectory_file"] = ""
    cfg["_abort"] = {}
    cfg.pop("_todo", None)
    cfg.pop("_plan_autocontinue", None)
    # Workers are ephemeral: don't let them write resume snapshots or the session event log
    # to the parent's paths (else the user's TUI offers to "resume" a swarm-worker context).
    cfg["resume"] = False
    cfg.pop("resume_path", None)
    cfg.pop("event_log_path", None)
    turns = 0
    for ev in agent_run(objective, state, cfg, system_prompt):
        if isinstance(ev, TurnDone):
            turns += 1
    return RunResult(summary=_last_assistant(state),
                     in_tokens=int(getattr(state, "total_input_tokens", 0) or 0),
                     out_tokens=int(getattr(state, "total_output_tokens", 0) or 0),
                     turns=int(getattr(state, "turn_count", 0) or turns))


def _last_assistant(state) -> str:
    for m in reversed(getattr(state, "messages", []) or []):
        if isinstance(m, dict) and m.get("role") == "assistant":
            c = (m.get("content") or "").strip()
            if c:
                return c[:4000]
    return ""


@dataclass
class WorkerOutcome:
    agent: str
    ok: bool
    candidate_id: str = ""
    commit: str = ""
    files_changed: int = 0
    summary: str = ""
    error: str = ""


def run_worker(bb: Blackboard, repo: str, base_ref: str, agent_id: str, objective: str, *,
               role: str = "builder", system_prompt: str = "", allow=BUILDER_TOOLS,
               base_config: dict | None = None, max_turns: int = 40, max_tool_calls: int = 40,
               runner: AgentRunner = default_agent_runner) -> WorkerOutcome:
    """Run one worker to a candidate: worktree → agent → snapshot → recorded candidate.

    Robust by construction (§32): a worker that raises is caught and recorded as a failed
    outcome; it never takes down the swarm. The worktree is left in place for the verifier
    (§18) — the coordinator cleans up losers and keeps/integrates the winner."""
    bb.emit("AGENT_STARTED", agent=agent_id, role=role, objective=objective)
    # Worktrees live OUTSIDE the repo (eratchet pattern): nesting one inside the repo's
    # own working tree breaks git and pollutes the main status. The durable artifact is
    # the snapshot commit in the object store, not this scratch tree. `wt` is a not-yet-
    # existing subdir of a temp parent, so `git worktree add` creates it cleanly.
    parent = tempfile.mkdtemp(prefix=f"swarm-{agent_id}-")
    wt = Path(parent) / "wt"
    if not add_worktree(repo, base_ref, wt):
        bb.emit("AGENT_FAILED", agent=agent_id, error="worktree_create_failed")
        _rmtree(parent)
        return WorkerOutcome(agent=agent_id, ok=False, error="worktree_create_failed")

    summary, error = "", ""
    rr = RunResult(summary="")
    try:
        res = runner(objective, str(wt), base_config or {}, system_prompt,
                     list(allow), max_turns, max_tool_calls)
        rr = res if isinstance(res, RunResult) else RunResult(summary=res or "")
        summary = rr.summary
    except Exception as e:  # noqa: BLE001 — one worker's crash must not sink the swarm (§32)
        error = f"{type(e).__name__}: {e}"
        bb.emit("AGENT_FAILED", agent=agent_id, error=error)

    commit, files_changed = snapshot_worktree(wt)
    cand = bb.add_candidate(agent=agent_id, summary=summary, worktree=str(wt),
                            base_ref=base_ref, commit=commit, files_changed=files_changed,
                            status=CAND_DRAFT if commit else CAND_REJECTED,
                            in_tokens=rr.in_tokens, out_tokens=rr.out_tokens, turns=rr.turns)
    bb.emit("AGENT_TERMINATED", agent=agent_id, candidate=cand.id,
            files_changed=files_changed, ok=bool(commit) and not error,
            in_tokens=rr.in_tokens, out_tokens=rr.out_tokens, turns=rr.turns)
    return WorkerOutcome(agent=agent_id, ok=bool(commit) and not error, candidate_id=cand.id,
                         commit=commit, files_changed=files_changed, summary=summary, error=error)


# ── diversity injection (§15): make agents cover the search space, not duplicate ──
_BUILDER_SYSTEM = (
    "You are ONE worker in a swarm of agents solving a shared objective. Work only in your "
    "own working directory; produce a concrete, minimal change that solves the objective and "
    "leave the tree in a state a test suite could verify. Do not narrate — act."
)

# Distinct angles so N builders explore different regions of the solution space (§15).
DIVERSITY_ANGLES = (
    "Take the simplest, most likely-correct approach first.",
    "Assume the obvious explanation is wrong; look for a subtler root cause.",
    "Suspect a dependency, version, or configuration problem and check that first.",
    "Trace backward from the failing behavior/test to its origin before changing anything.",
    "Inspect the most recent changes for what broke, and target those.",
    "Build a minimal reproduction, then fix the smallest thing that makes it pass.",
    "Consider concurrency, ordering, or state/caching issues.",
    "Attempt a fully independent solution without assuming the existing structure is right.",
)


def diversify(objective: str, n: int) -> list[tuple[str, str]]:
    """Return n (angle_label, system_prompt) pairs — each worker gets a different angle so
    the swarm covers the space instead of making n copies of the same mistake (§39)."""
    out: list[tuple[str, str]] = []
    for i in range(max(1, n)):
        angle = DIVERSITY_ANGLES[i % len(DIVERSITY_ANGLES)]
        out.append((angle, f"{_BUILDER_SYSTEM}\n\nApproach for this worker: {angle}"))
    return out


# ── independent verification (§18) + evidence-based judging (§20) ─────────────
# verify_fn(candidate) -> (passed, total). Default runs a shell command in the worktree.
VerifyFn = Callable[["Candidate"], "tuple[int, int]"]


def make_shell_verifier(verify_cmd: str, fitness: str = "auto",
                        timeout: int = 600) -> VerifyFn:
    """A verifier that runs `verify_cmd` in the candidate's worktree and scores the output
    with drydock.ratchet.score_output — the same scorer the ratchet/eratchet use, so a
    candidate is judged by a real test run, never by the builder's self-report (§18/§20)."""
    from drydock.ratchet import run_shell_bounded, score_output

    def verify(cand: Candidate) -> tuple[int, int]:
        if not cand.worktree or not Path(cand.worktree).exists():
            return 0, 0
        try:
            out, rc, _ = run_shell_bounded(verify_cmd, cand.worktree, timeout)
        except (OSError, subprocess.SubprocessError):
            return 0, 1
        # a hung candidate (killed at the timeout) scores whatever finished before the kill
        return score_output(out, fitness, rc)

    return verify


def verify_candidate(bb: Blackboard, cand: Candidate, verify: VerifyFn) -> Candidate:
    """Score one candidate independently and update its evidence on the blackboard."""
    passed, total = verify(cand)
    status = cand.status
    if total > 0:
        status = CAND_PROMISING if passed >= total else CAND_REJECTED
    bb.update("candidates", cand.id, tests_passed=passed, tests_total=total,
              verifications=cand.verifications + 1, status=status)
    bb.emit("TEST_COMPLETED", candidate=cand.id, passed=passed, total=total, status=status)
    updated = next((c for c in bb.candidates() if c.id == cand.id), cand)
    return updated


def _evidence_key(c: Candidate):
    """Higher is better. Candidates compete on EVIDENCE, not confidence (§20): a real
    patch (commit) that passes the most tests, at the highest pass-ratio, then the simplest
    (fewest files), then the most independently verified."""
    has_patch = 1 if c.commit else 0
    ratio = (c.tests_passed / c.tests_total) if c.tests_total > 0 else 0.0
    return (has_patch, ratio, c.tests_passed, -c.files_changed, c.verifications)


def judge(candidates: list[Candidate]) -> Candidate | None:
    """Pick the strongest candidate by evidence, or None if none has a patch."""
    real = [c for c in candidates if c.commit]
    if not real:
        return None
    return max(real, key=_evidence_key)


# ── candidate-population diversity (write-back-corpus signal, §33/§34) ─────────
# The swarm's likeliest value for the ratchet is NOT a better single solve but a more
# DIVERSE set of verified diffs to self-distill on (this programme's lever is write-back,
# not search). These measure that: how many DISTINCT verified solutions a run produced.
def candidate_diff(repo: str | Path, commit: str, base_ref: str = "HEAD") -> str:
    """The worker's change as a unified diff (base_ref..commit). '' on error. Works after
    the worktree is torn down — the commit lives in the object store."""
    if not commit:
        return ""
    try:
        r = _git(["diff", f"{base_ref}..{commit}"], repo)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _norm_diff(diff: str) -> str:
    """Drop diff headers/hunk markers + blank lines so cosmetically-different but
    semantically-identical patches dedupe to the same fingerprint."""
    keep = []
    for ln in diff.splitlines():
        if ln.startswith(("diff --git", "index ", "--- ", "+++ ", "@@ ")):
            continue
        s = ln.strip()
        if s:
            keep.append(s)
    return "\n".join(keep)


def candidate_diversity(repo: str | Path, candidates: list[Candidate], base_ref: str = "HEAD",
                        verified_only: bool = True) -> dict:
    """Diversity of the candidate population. `verified_only` restricts to candidates that
    passed all their tests (the corpus a write-back run would actually train on). Returns
    verified count, distinct-solution count (by normalized-diff fingerprint), and the ratio.
    A swarm that produces N verified-but-identical diffs has diversity 1/N — no more
    training signal than one ratchet solve."""
    pool = [c for c in candidates if c.commit]
    if verified_only:
        pool = [c for c in pool if c.tests_total > 0 and c.tests_passed >= c.tests_total]
    fingerprints = set()
    for c in pool:
        nd = _norm_diff(candidate_diff(repo, c.commit, base_ref))
        if nd:
            fingerprints.add(hashlib.sha256(nd.encode("utf-8")).hexdigest())
    n_verified = len(pool)
    n_distinct = len(fingerprints)
    return {"verified": n_verified, "distinct": n_distinct,
            "diversity_ratio": (n_distinct / n_verified) if n_verified else 0.0}


def _peer_notes(candidates: list[Candidate], limit: int = 8) -> str:
    """A compact 'what peers already tried' digest for blackboard consumption (§10/§31):
    each attempt's verdict + verified score + one-line summary, so a reader builds on the
    partials and avoids repeating the failures. Only summaries cross between agents, never
    full transcripts (§8 — preserve independence while still comparing notes)."""
    # Rank strongest-first so the reader sees the best partial at the top, and explicitly flag
    # it as the base to EXTEND — the actionable core of cross-pollination (§14): don't restart
    # from scratch, continue the leading partial (and skip the failed approaches below it).
    ranked = sorted(candidates, key=lambda c: (c.tests_passed, c.tests_total), reverse=True)[:limit]
    lines = []
    for i, c in enumerate(ranked):
        score = f"{c.tests_passed}/{c.tests_total}" if c.tests_total else "unverified"
        solved = bool(c.tests_total and c.tests_passed >= c.tests_total)
        verdict = "SOLVED" if solved else ("partial" if c.tests_passed else "failed")
        summ = (c.summary or "").strip().splitlines()[0][:160] if c.summary else "(no summary)"
        tag = "  ⟵ STRONGEST partial — EXTEND this approach" if (i == 0 and c.tests_passed and not solved) else ""
        lines.append(f"- {c.agent} [{verdict} {score}]{tag}: {summ}")
    return "\n".join(lines)


def should_auto_escalate(goal: str, verify_cmd: str, fail_streak: int, *,
                         is_git_repo: bool, already_escalated: bool) -> bool:
    """Decide whether the harness should auto-escalate a stuck single-agent turn into a
    swarm (escalate-on-difficulty — the harness decides, no user command). True only when:
    a KNOWN verifier has failed enough times in a row (reusing the ratchet's threshold, so
    the task is hard AND verifiable), the repo supports worktree isolation, and we haven't
    already escalated this session. This is the whole trigger policy in one testable place."""
    if already_escalated or not is_git_repo or not verify_cmd:
        return False
    try:
        from drydock.ratchet import ratchet_offer
        return ratchet_offer(goal, verify_cmd, fail_streak) is not None
    except Exception:  # noqa: BLE001 — a decision helper must never raise into the turn loop
        return False


@dataclass
class SwarmResult:
    swarm_id: str
    objective: str
    root: str
    converged: bool
    winner: Candidate | None
    candidates: list[Candidate]


# Auto-swarm triviality gate: route a task to the swarm only when it looks like a
# substantial, self-contained implementation — never a one-line fix, question, or read.
_SUBSTANTIAL = ("implement", "build the", "build a", "refactor", "rewrite", "create the",
                "all functions", "each function", "whole module", "entire", "feature",
                "make the tests", "tests pass", "test suite", "port ", "migrate",
                "add support", "write the", "flesh out", "scaffold", "from scratch")
_TRIVIAL = ("typo", "rename", "one line", "one-line", "single line", "the comment",
            "what is", "what's", "explain", "why ", "show me", "read ", "print the",
            "list the", "how do", "how does", "?")


def looks_substantial(task: str) -> bool:
    """Heuristic gate for auto-swarm (config `auto_swarm`): True only when the task is a
    substantial, self-contained implementation worth parallel attempts — not a trivial edit,
    a question, or a read. Conservative: unsure → False (single agent stays the default)."""
    t = (task or "").lower().strip()
    if len(t.split()) < 4:
        return False
    if any(x in t for x in _TRIVIAL):
        return False
    return any(x in t for x in _SUBSTANTIAL)


def run_swarm(cwd: str | Path, objective: str, *, agents: "int | str" = 4,
              base_config: dict | None = None,
              base_ref: str = "HEAD", verify_cmd: str | None = None, fitness: str = "auto",
              max_turns: int = 40, max_tool_calls: int = 40, max_workers: int | None = None,
              runner: AgentRunner = default_agent_runner, verify: VerifyFn | None = None,
              on_event: "Callable[[str, dict], None] | None" = None,
              share: bool = False, waves: int = 2,
              swarm_id: str | None = None, cancel=None) -> SwarmResult:
    """Coordinate a parallel-strategy swarm end to end (§24 Parallel, the MVP default):
    decompose into N diversity-injected Builders (§15), fan them out concurrently over one
    shared inference server (§22), verify each candidate independently (§18), and converge on
    the best by evidence (§20). Never raises on an individual worker — failures are contained
    (§32). `runner`/`verify` are injectable for testing without a live model. `cancel` (a
    threading.Event) stops the swarm: running agents end their turn loop, queued ones never
    start, and verification is skipped — whatever was produced is still judged."""
    from concurrent.futures import ThreadPoolExecutor

    repo = repo_root(cwd)
    if repo is None:
        raise ValueError("swarm needs a git repository (run `git init` first) for worktree "
                         "isolation")
    bc = base_config or {}
    if cancel is not None:
        # workers inherit this as their agent-loop stop signal (agent.run checks _cancel)
        base_config = bc = {**bc, "_cancel": cancel}

    def _cancelled() -> bool:
        return cancel is not None and cancel.is_set()
    # Auto-size to the server's real concurrency (§21/§22): hammer a big box, tone down a
    # small one. N = min(hardware concurrency, task-demand, budget) — never maximized.
    detected = 0
    if isinstance(agents, str) and agents == "auto":
        from drydock.capacity import detect_concurrency, swarm_size
        detected = detect_concurrency(str(bc.get("base_url", "")),
                                      provider=str(bc.get("provider", "vllm")),
                                      model=str(bc.get("model", "")), config=bc)
        n = swarm_size(detected)
    else:
        n = max(1, int(agents))
    bb = create_swarm(repo, objective, {"agents": n, "strategy": "parallel",
                                        "verify_cmd": verify_cmd or "", "fitness": fitness,
                                        "concurrency_detected": detected},
                      swarm_id=swarm_id)
    bb.emit("SWARM_START", agents=n, strategy="parallel", base_ref=base_ref,
            concurrency_detected=detected)

    def _ev(kind: str, **d) -> None:
        if on_event is not None:
            try:
                on_event(kind, d)
            except Exception:  # noqa: BLE001 — a UI callback must never break the swarm
                pass

    _ev("start", agents=n, objective=objective)

    # Resolve the verifier once (auto-detect if not given) so scoring is identical per arm.
    if verify is None:
        vc = verify_cmd
        if not vc:
            try:
                from drydock.ratchet import detect_verifier
                found = detect_verifier(repo)
                if found:
                    vc, fitness = found
            except Exception:  # noqa: BLE001 — detection is best-effort
                vc = None
        verify = make_shell_verifier(vc, fitness) if vc else None
        if vc:
            bb.emit("VERIFIER_RESOLVED", verify_cmd=vc, fitness=fitness)
        else:
            bb.emit("VERIFIER_MISSING")

    # 1. decompose into diversity-injected Builder tasks (§6, §15).
    angles = diversify(objective, n)
    for i, (angle, _sys) in enumerate(angles):
        bb.add_task(role="builder", objective=objective, assignee=f"agent-{i + 1}",
                    status=TASK_ASSIGNED)
        bb.emit("TASK_ASSIGNED", agent=f"agent-{i + 1}", angle=angle)

    # 2. fan out workers over the shared server (§22). Each is isolated + safe. `extra_sys`
    # carries peers' notes (blackboard consumption, §10) for waves after the first.
    # Workers are full coding agents: give them drydock's own coding prompt (tool use,
    # verify-before-done, act-don't-plan), then the swarm role + diversity angle on top.
    # Without it a worker got only the 3-line role text and tended to plan until it ran
    # out of output tokens instead of editing.
    from drydock.tuning import system_prompt_for_model
    base_sys = system_prompt_for_model(str(bc.get("model") or ""), worker=True) + "\n\n"

    def _spawn(i: int, extra_sys: str) -> WorkerOutcome:
        if _cancelled():
            return WorkerOutcome(agent=f"agent-{i + 1}", ok=False, error="cancelled")
        angle, sysprompt = angles[i]
        out = run_worker(bb, repo, base_ref, f"agent-{i + 1}", objective,
                         role="builder", system_prompt=base_sys + sysprompt + extra_sys,
                         base_config=base_config, max_turns=max_turns,
                         max_tool_calls=max_tool_calls, runner=runner)
        _ev("worker_done", agent=out.agent, ok=out.ok, files=out.files_changed,
            commit=out.commit, error=out.error)
        return out

    def _verify_unscored() -> None:
        """Independently score any candidate not yet verified (§18)."""
        if verify is None or _cancelled():
            return
        for c in bb.candidates():
            if c.commit and c.tests_total == 0:
                vc = verify_candidate(bb, c, verify)
                _ev("verified", candidate=vc.id, passed=vc.tests_passed,
                    total=vc.tests_total, status=vc.status)

    workers = max_workers or min(n, 8)
    if share and waves > 1 and n >= 2:
        # BLACKBOARD CONSUMPTION (§10): run in waves. Wave 0 explores blind (independence,
        # §8); each later wave READS peers' verified attempts, so agents compare notes and
        # build on partials instead of repeating failures. Verify after each wave so the
        # notes the next wave reads are already scored.
        per = -(-n // max(2, waves))  # ceil(n / waves)
        i0, w = 0, 0
        while i0 < n and not _cancelled():
            idxs = list(range(i0, min(i0 + per, n)))
            i0 += per
            notes = _peer_notes(bb.candidates()) if w > 0 else ""
            extra = (f"\n\nWhat other agents already tried on this SAME objective (build on the "
                     f"partials; do NOT repeat the failed approaches):\n{notes}") if notes else ""
            if notes:
                _ev("share", wave=w, agents=len(idxs))
            with ThreadPoolExecutor(max_workers=min(len(idxs), workers)) as ex:
                list(ex.map(lambda i: _spawn(i, extra), idxs))
            _verify_unscored()   # score this wave before the next reads it
            w += 1
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(lambda i: _spawn(i, ""), range(n)))

    # 3. verify any remaining candidates independently (§18) — the builder never grades itself.
    _verify_unscored()

    # 4. judge on evidence (§20) and check convergence (§19).
    if _cancelled():
        bb.emit("SWARM_CANCELLED")
        _ev("cancelled")
    final = bb.candidates()
    winner = judge(final)
    converged = bool(winner and winner.tests_total > 0 and winner.tests_passed == winner.tests_total)
    if winner is not None:
        status = CAND_ACCEPTED if converged else winner.status
        bb.update("candidates", winner.id, status=status)
        bb.emit("JUDGE_DECISION", candidate=winner.id, converged=converged,
                tests_passed=winner.tests_passed, tests_total=winner.tests_total)
    if converged:
        bb.emit("SWARM_CONVERGED", candidate=winner.id if winner else "")
    _ev("judge", winner=winner.id if winner else "", converged=converged,
        agent=winner.agent if winner else "",
        commit=winner.commit if winner else "",
        passed=winner.tests_passed if winner else 0,
        total=winner.tests_total if winner else 0)

    bb.set_metrics({"agents": n, "candidates": len(final),
                    "candidates_with_patch": len([c for c in final if c.commit]),
                    "converged": converged,
                    "winner": winner.id if winner else "",
                    # matched-compute ledger (§21/§33): the cost side of any swarm-vs-eratchet
                    # comparison — a solve that spent 3x the tokens is not a fair win.
                    "total_in_tokens": sum(c.in_tokens for c in final),
                    "total_out_tokens": sum(c.out_tokens for c in final),
                    "total_turns": sum(c.turns for c in final),
                    "ts": time.time()})

    # Tear down losers' scratch worktrees (§16) — their commits are durable in the object
    # store, so nothing is lost. Keep the winner's tree for inspection/integration.
    for c in final:
        if c.worktree and (winner is None or c.id != winner.id):
            remove_worktree(repo, c.worktree)
            bb.update("candidates", c.id, worktree="")

    # Re-read the winner so its ACCEPTED status is reflected in the returned object.
    if winner is not None:
        winner = next((c for c in bb.candidates() if c.id == winner.id), winner)
    return SwarmResult(swarm_id=bb.root.name, objective=objective, root=str(bb.root),
                       converged=converged, winner=winner, candidates=bb.candidates())


# ── terminal status display (§26) ────────────────────────────────────────────
def render_status(bb: Blackboard) -> str:
    """A compact text view of one swarm's state (§26). Reused by the end-of-run summary
    and the `drydock swarm status` subcommand; reads only the persisted blackboard, so it
    works on a finished or interrupted swarm alike."""
    cands = bb.candidates()
    tasks = bb.tasks()
    metrics = bb.metrics()
    winner = judge(cands)
    lines = [
        f"DRYDOCK SWARM  {bb.root.name}",
        f"Objective:  {bb.objective()}",
        f"Agents:     {len(tasks)} spawned    Candidates: {len(cands)} "
        f"({len([c for c in cands if c.commit])} with a patch)",
        f"Converged:  {bool(metrics.get('converged'))}",
        "",
        "Candidates (strongest first):",
    ]
    if not cands:
        lines.append("  (none yet)")
    for c in sorted(cands, key=_evidence_key, reverse=True):
        mark = "*" if winner and c.id == winner.id else " "
        tests = f"{c.tests_passed}/{c.tests_total}" if c.tests_total else "—"
        lines.append(f" {mark} {c.id:5} {c.agent:9} tests={tests:>7} "
                     f"files={c.files_changed:<3} {c.status}")
    if winner is not None:
        lines += ["", f"Winner: {winner.id} by {winner.agent} — {winner.status}"]
        if winner.commit:
            lines.append(f"  commit {winner.commit[:12]} in {winner.worktree or '(worktree removed)'}")
        if winner.summary:
            lines.append(f"  {winner.summary[:200]}")
    return "\n".join(lines)


# ── `drydock swarm` subcommand (§25) ─────────────────────────────────────────
def run_cli(argv: list, config: dict | None = None) -> int:
    """Entry point for `drydock swarm ...`. Subcommands: solve (default), status, list,
    resume. Returns a process exit code (0 ok, 2 converged-with-winner is still 0; non-zero
    only on usage/setup errors), mirroring eratchet.run_cli."""
    import argparse

    config = config or {}
    cwd = config.get("cwd") or os.getcwd()
    argv = list(argv or [])

    if argv and argv[0] == "list":
        ids = list_swarms(cwd)
        print("\n".join(ids) if ids else "(no swarms in this project)")
        return 0

    if argv and argv[0] == "status":
        sid = argv[1] if len(argv) > 1 else latest_swarm(cwd)
        if not sid:
            print("No swarms yet. Run: drydock swarm \"<objective>\" --agents N")
            return 1
        print(render_status(open_swarm(cwd, sid)))
        return 0

    if argv and argv[0] == "resume":
        sid = argv[1] if len(argv) > 1 else latest_swarm(cwd)
        if not sid:
            print("Nothing to resume.")
            return 1
        return _resume(cwd, sid, config)

    # default: solve
    p = argparse.ArgumentParser(prog="drydock swarm", add_help=True,
                                description="Coordinate a swarm of agents against one objective.")
    p.add_argument("objective", nargs="*", help="the problem to solve")
    p.add_argument("--agents", type=int, default=4, help="number of worker agents (2-8 for MVP)")
    p.add_argument("--verify", default=None, help="test/verify command (auto-detected if omitted)")
    p.add_argument("--fitness", default="auto", help="score mode for --verify (auto|exitcode|regex)")
    p.add_argument("--base-ref", default="HEAD", help="git ref each worker branches from")
    p.add_argument("--max-turns", type=int, default=40)
    p.add_argument("--max-tool-calls", type=int, default=40)
    p.add_argument("--max-workers", type=int, default=None, help="max concurrent workers")
    p.add_argument("--share", action="store_true",
                   help="blackboard cross-pollination: run in waves; later agents read peers' "
                        "verified attempts and build on partials instead of repeating failures")
    p.add_argument("--waves", type=int, default=2,
                   help="number of waves when --share is set (wave 0 explores blind; default 2)")
    try:
        args = p.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)

    objective = " ".join(args.objective).strip()
    if not objective:
        p.print_usage()
        print('\nExample: drydock swarm "fix the failing auth tests" --agents 4')
        return 1
    if repo_root(cwd) is None:
        print("Swarm needs a git repository for per-agent worktree isolation. Run `git init` first.")
        return 1

    print(f"⚓ Drydock swarm — {args.agents} agents on: {objective}")
    print("   (isolated git worktrees, independent verification, evidence-based judging)"
          + (f"\n   🔗 shared communication ON — {args.waves} waves, peers' notes cross the "
             "blackboard\n" if args.share else "\n"))

    def _on(kind: str, d: dict) -> None:
        if kind == "share":
            print(f"  🔗 wave {d.get('wave')}: {d.get('agents')} agent(s) reading peers' notes")
        elif kind == "worker_done":
            print(f"  · {d.get('agent')}: {'ok' if d.get('ok') else 'failed'}"
                  + (f" ({d.get('files')} files)" if d.get('files') else ""))
        elif kind == "verified":
            print(f"  ✓ {d.get('candidate')}: {d.get('passed')}/{d.get('total')} ({d.get('status')})")
        elif kind == "judge":
            print(f"  ⚖ winner={d.get('agent') or '—'} converged={d.get('converged')}")

    try:
        res = run_swarm(cwd, objective, agents=args.agents, base_config=config,
                        base_ref=args.base_ref, verify_cmd=args.verify, fitness=args.fitness,
                        max_turns=args.max_turns, max_tool_calls=args.max_tool_calls,
                        max_workers=args.max_workers, share=args.share, waves=args.waves,
                        on_event=_on)
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    print("\n" + render_status(open_swarm(cwd, res.swarm_id)))
    if res.converged and res.winner is not None:
        print(f"\nSWARM CONVERGED. Apply the winner with:\n"
              f"  git cherry-pick {res.winner.commit}")
    elif res.winner is not None:
        print(f"\nBest candidate: {res.winner.id} (not fully verified — inspect "
              f"{res.winner.commit[:12]} before applying).")
    else:
        print("\nNo candidate produced a usable patch.")
    return 0


def _resume(cwd: str, sid: str, config: dict) -> int:
    """Light MVP resume (§28): re-open a swarm, re-verify any candidate that was never
    scored, re-judge, and print status. Full worker re-spawn is Phase 2."""
    bb = open_swarm(cwd, sid)
    cfg = bb.config()
    vc = cfg.get("verify_cmd") or None
    print(f"Resuming {sid} — {bb.objective()}")
    if vc:
        verify = make_shell_verifier(vc, str(cfg.get("fitness") or "auto"))
        for c in bb.candidates():
            if c.commit and c.tests_total == 0:
                verify_candidate(bb, c, verify)
    print(render_status(bb))
    return 0

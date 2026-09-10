"""Mission execution engine — the controller loop (§41) + Worker/Evaluator/checkpoints.

The deterministic Mission Manager (§7.1) drives the durable state from drydock/mission.py:
per cycle it checkpoints (§18), runs ONE bounded Worker (§7.3), lets a deterministic
Evaluator (§7.4) decide KEEP vs REVERT (§19), tracks progress vs activity (§20), and stops
on success / budget / stagnation (§22/§30). Worker + Evaluator are injectable, so the loop
is testable with no model; the defaults use `agent.run` and the project verifier.
"""
from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from drydock import mission as M


@dataclass
class WorkerResult:
    ok: bool
    summary: str = ""
    in_tokens: int = 0
    out_tokens: int = 0
    error: str = ""


@dataclass
class Evaluation:
    accept: bool                 # KEEP (True) vs REVERT (False)
    metric_before: float = 0.0
    metric_after: float = 0.0
    passed: bool = False
    reason: str = ""
    confidence: float = 1.0


# worker(task, mission, cwd, base_config) -> WorkerResult
WorkerFn = Callable[[dict, dict, str, dict], WorkerResult]
# evaluator(task, mission, cwd, before) -> Evaluation
EvaluatorFn = Callable[[dict, dict, str, float], Evaluation]


# ── git checkpoints (§18) / auto-revert (§19) ─────────────────────────────────
def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120)


def checkpoint(repo: str) -> str:
    """Commit the current tree as a checkpoint and return its sha ('' if not a repo)."""
    try:
        _git(["add", "-A"], repo)
        _git(["-c", "user.name=drydock-mission", "-c", "user.email=mission@drydock",
              "commit", "--allow-empty", "--no-verify", "-m", "mission checkpoint"], repo)
        r = _git(["rev-parse", "HEAD"], repo)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def restore(repo: str, commit: str) -> bool:
    if not commit:
        return False
    try:
        ok = _git(["reset", "--hard", commit], repo).returncode == 0
        _git(["clean", "-fd"], repo)   # reset --hard leaves untracked files; drop them too
        return ok
    except (OSError, subprocess.SubprocessError):
        return False


# ── default Worker (bounded agent.run) + Evaluator (project verifier) ─────────
def default_worker(task: dict, mission: dict, cwd: str, base_config: dict) -> WorkerResult:
    """Run one bounded task via the in-process agent loop (the disposable Worker, §7.3)."""
    from drydock.agent import AgentState, TurnDone
    from drydock.agent import run as agent_run

    objective = task.get("objective", "")
    state = AgentState()
    cfg = dict(base_config or {})
    cfg["cwd"] = cwd
    allow = task.get("allowed_tools") or ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]
    cfg["tool_allowlist"] = list(allow)
    cfg["max_turns"] = int(task.get("max_turns", 30))
    cfg["trajectory_file"] = ""
    cfg["_abort"] = {}
    cfg["resume"] = False
    cfg.pop("resume_path", None)
    system_prompt = task.get("system_prompt", "")
    neg = task.get("negative_knowledge") or []
    if neg:
        system_prompt += ("\n\nKNOWN FAILED APPROACHES on this mission — do NOT repeat them "
                          "without new evidence:\n" + "\n".join(f"- {n}" for n in neg))
    try:
        for ev in agent_run(objective, state, cfg, system_prompt):
            _ = isinstance(ev, TurnDone)
    except Exception as e:  # noqa: BLE001 — a worker crash is contained by the loop (§32)
        return WorkerResult(ok=False, error=f"{type(e).__name__}: {e}")
    summ = ""
    for msg in reversed(getattr(state, "messages", []) or []):
        if isinstance(msg, dict) and msg.get("role") == "assistant" and (msg.get("content") or "").strip():
            summ = str(msg["content"]).strip()[:2000]
            break
    return WorkerResult(ok=True, summary=summ,
                        in_tokens=int(getattr(state, "total_input_tokens", 0) or 0),
                        out_tokens=int(getattr(state, "total_output_tokens", 0) or 0))


def make_verifier_evaluator(verify_cmd: str, fitness: str = "auto",
                            timeout: int = 1800) -> EvaluatorFn:
    """Deterministic Evaluator (§7.4): run the project's own check, score it, KEEP only if it
    did not regress the metric (§19). Never the builder's word — a real measurement."""
    from drydock.ratchet import score_output

    def evaluate(task: dict, mission: dict, cwd: str, before: float) -> Evaluation:
        try:
            r = subprocess.run(verify_cmd, cwd=cwd, shell=True, capture_output=True,
                               text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            return Evaluation(accept=False, metric_before=before, reason="verifier failed to run")
        passed, total = score_output((r.stdout or "") + (r.stderr or ""), fitness, r.returncode)
        after = 100.0 * passed / total if total else (100.0 if r.returncode == 0 else 0.0)
        accept = after >= before          # KEEP forward progress; REVERT regressions (§19)
        return Evaluation(accept=accept, metric_before=before, metric_after=after,
                          passed=bool(total and passed >= total),
                          reason=f"metric {before:.1f}→{after:.1f} ({passed}/{total})")
    return evaluate


# ── success check (§30) ───────────────────────────────────────────────────────
def _cmp(cur: float, op: str, num: float) -> bool:
    return {">=": cur >= num, ">": cur > num, "<=": cur <= num, "<": cur < num,
            "==": cur == num}.get(op, False)


def success_met(success_criteria: dict | None, current_metric: float | None) -> bool:
    """True only if at least one numeric criterion is defined AND all numeric ones are met.
    Non-numeric criteria (e.g. 'pass') are ignored in the MVP."""
    evaluated = False
    for v in (success_criteria or {}).values():
        if isinstance(v, str):
            mm = re.match(r"\s*(>=|>|<=|<|==)\s*(-?[\d.]+)", v.strip())
            if mm:
                if current_metric is None:
                    return False
                evaluated = True
                if not _cmp(current_metric, mm.group(1), float(mm.group(2))):
                    return False
    return evaluated


# ── one task: checkpoint → worker → evaluate → keep/revert (§17/§19) ──────────
@dataclass
class TaskOutcome:
    task_id: str
    accept: bool
    metric_after: float
    reverted: bool
    error: str = ""


def run_task(store: M.MissionStore, task: dict, *, mission: dict, cwd: str, repo: str,
             worker: WorkerFn, evaluator: EvaluatorFn, base_config: dict | None = None,
             worker_id: str = "worker-1") -> TaskOutcome | None:
    """Execute one leased task with checkpoint + KEEP/REVERT. None if the lease was lost."""
    mid = mission["id"]
    tid = task["id"]
    if not store.claim_task(tid, worker_id):
        return None
    before = mission.get("current_metric") or 0.0
    cp = checkpoint(repo) if repo else ""
    store.event(mid, "task_started", task=tid, objective=task.get("objective", ""))
    # Context reconstruction (§13): surface known failed approaches so the worker doesn't
    # repeat them (§16). Relevance by content-word overlap with this task.
    neg = store.similar_knowledge(mid, task.get("objective", ""), type=M.K_NEGATIVE, top=3)
    task = dict(task)
    task["negative_knowledge"] = [k["statement"] for k in neg]
    t0 = time.monotonic()
    wr = worker(task, mission, cwd, base_config or {})
    store.add_usage(mid, tokens=wr.in_tokens + wr.out_tokens, wall_s=time.monotonic() - t0)
    if not wr.ok:
        if repo and cp:
            restore(repo, cp)
        store.complete_task(tid, M.T_FAILED, {"error": wr.error})
        return TaskOutcome(tid, accept=False, metric_after=before, reverted=True, error=wr.error)

    ev = evaluator(task, mission, cwd, before)
    store.add_usage(mid, experiments=1)
    if ev.accept:
        end = checkpoint(repo) if repo else ""
        store.set_metric(mid, ev.metric_after)
        store.complete_task(tid, M.T_COMPLETED,
                            {"summary": wr.summary, "metric": ev.metric_after, "decision": "KEEP",
                             "commit": end})
        store.event(mid, "experiment", task=tid, decision="KEEP",
                    metric_before=ev.metric_before, metric_after=ev.metric_after, commit=end)
        if wr.summary:
            store.add_knowledge(mid, M.K_FINDING,
                                f"KEPT (metric {ev.metric_before:.1f}→{ev.metric_after:.1f}): "
                                f"{wr.summary[:280]}", confidence=0.7, sources=[tid])
    else:
        if repo and cp:
            restore(repo, cp)      # auto-revert the regression (§19)
        store.complete_task(tid, M.T_COMPLETED,
                            {"summary": wr.summary, "decision": "REVERT", "reverted": True})
        # negative knowledge stays in history even though the code is reverted (§16/§19)
        store.event(mid, "experiment", task=tid, decision="REVERT",
                    metric_before=ev.metric_before, metric_after=ev.metric_after, reason=ev.reason)
        # Key the statement on the *approach* (task objective) so a later proposal that
        # resembles it is caught by similar_knowledge (§16/AT-8); the summary/reason add detail.
        approach = task.get("objective", "")
        detail = " ".join(x for x in (wr.summary, ev.reason) if x)[:280]
        store.add_knowledge(mid, M.K_NEGATIVE,
                            f"REVERTED (metric {ev.metric_before:.1f}→{ev.metric_after:.1f}, "
                            f"no improvement): {approach}. {detail}", confidence=0.6, sources=[tid])
    return TaskOutcome(tid, accept=ev.accept, metric_after=ev.metric_after, reverted=not ev.accept)


def establish_baseline(store: M.MissionStore, mission_id: str, cwd: str,
                       evaluator: EvaluatorFn) -> float | None:
    """Measure the starting metric once and record it as the IMMUTABLE baseline (§9), before
    any improvement — so later experiments compare against it and progress ≠ activity (§20).
    No-op if a baseline already exists."""
    m = store.get_mission(mission_id)
    if not m or (m.get("baseline") or {}).get("metric") is not None:
        return None
    store.set_status(mission_id, M.M_BASELINING)
    ev = evaluator({}, m, cwd, 0.0)
    store.set_baseline(mission_id, {"metric": ev.metric_after, "passed": ev.passed})
    store.set_metric(mission_id, ev.metric_after)
    store.event(mission_id, "baseline_established", metric=ev.metric_after)
    return ev.metric_after


# ── the mission controller loop (§41) ─────────────────────────────────────────
@dataclass
class RunSummary:
    mission_id: str
    status: str
    cycles: int
    final_metric: float | None
    events: list = field(default_factory=list)


def run_mission(store: M.MissionStore, mission_id: str, *, cwd: str, repo: str = "",
                worker: WorkerFn = default_worker, evaluator: EvaluatorFn | None = None,
                planner: Callable[[M.MissionStore, dict], int] | None = None,
                base_config: dict | None = None, worker_id: str = "worker-1",
                max_cycles: int = 10000, stagnation_limit: int = 5,
                on_event: Callable[[str, dict], None] | None = None) -> RunSummary:
    """Drive a mission to a stopping condition with a SINGLE worker (§41/§49). Stops on
    success (§30), budget (§30), stagnation (§22), or an empty queue (planner is a later
    slice). Never relies on the worker staying coherent across the whole mission (§50)."""
    def _ev(kind: str, **d) -> None:
        if on_event is not None:
            try:
                on_event(kind, d)
            except Exception:  # noqa: BLE001
                pass

    store.set_status(mission_id, M.M_EXECUTING)
    no_progress = 0
    cycles = 0
    while cycles < max_cycles:
        cycles += 1
        m = store.get_mission(mission_id)
        if not m or m["status"] in M._TERMINAL:
            break
        done, why = store.budget_exhausted(mission_id)
        if done:
            store.set_status(mission_id, M.M_BUDGET_EXHAUSTED)
            store.event(mission_id, "stopped", reason=why)
            _ev("stopped", reason=why)
            break
        if success_met(m.get("success_criteria"), m.get("current_metric")):
            store.set_status(mission_id, M.M_COMPLETED)
            store.event(mission_id, "completed", metric=m.get("current_metric"))
            _ev("completed", metric=m.get("current_metric"))
            break
        store.reclaim_expired(mission_id)
        ready = store.ready_tasks(mission_id)
        if not ready:
            added = planner(store, m) if planner else 0
            if added:
                store.event(mission_id, "replan", added=added)
                _ev("replan", added=added)
                continue
            store.event(mission_id, "no_ready_tasks")
            _ev("idle", reason="no ready tasks")
            break
        task = ready[0]
        out = run_task(store, task, mission=m, cwd=cwd, repo=repo, worker=worker,
                       evaluator=(evaluator or (lambda *_: Evaluation(accept=True))),
                       base_config=base_config, worker_id=worker_id)
        progressed = bool(out and out.accept)
        _ev("task_done", task=task["id"], accepted=progressed,
            metric=out.metric_after if out else None)
        no_progress = 0 if progressed else no_progress + 1
        if no_progress >= stagnation_limit:
            store.set_status(mission_id, M.M_BLOCKED)
            store.event(mission_id, "stagnation", cycles=no_progress)
            _ev("stagnation", cycles=no_progress)
            break
    m = store.get_mission(mission_id) or {}
    return RunSummary(mission_id=mission_id, status=m.get("status", "?"), cycles=cycles,
                      final_metric=m.get("current_metric"))

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
from fnmatch import fnmatch
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


# The mission's own state DB lives under .drydock/ inside the repo it checkpoints. It must
# NEVER be swept into checkpoints or rolled back by a REVERT — else `git reset --hard` /
# `git clean` would corrupt or erase live mission state. Every checkpoint/restore excludes it.
_MISSION_WS = ".drydock"


def checkpoint(repo: str) -> str:
    """Commit the current tree (EXCEPT the mission workspace) and return its sha ('' if not a
    repo). Excluding .drydock keeps the mission's durable state out of the code checkpoints."""
    try:
        _git(["add", "-A", "--", ".", f":(exclude){_MISSION_WS}"], repo)
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
        # reset --hard leaves untracked files; drop them too — but NEVER the mission workspace.
        _git(["clean", "-fd", "-e", _MISSION_WS], repo)
        return ok
    except (OSError, subprocess.SubprocessError):
        return False


def changed_paths(repo: str, since_commit: str) -> set[str]:
    """Repo-relative paths the worker touched this experiment: tracked diff vs the pre-worker
    checkpoint plus new untracked files. Used to guard the measurement apparatus (§7.4)."""
    if not (repo and since_commit):
        return set()
    out: set[str] = set()
    try:
        d = _git(["diff", "--name-only", since_commit], repo)
        if d.returncode == 0:
            out.update(p for p in d.stdout.splitlines() if p.strip())
        u = _git(["ls-files", "--others", "--exclude-standard"], repo)
        if u.returncode == 0:
            out.update(p for p in u.stdout.splitlines() if p.strip())
    except (OSError, subprocess.SubprocessError):
        return set()
    return out


def tampered_paths(changed: "set[str] | list[str]", protected: list[str]) -> list[str]:
    """Which changed paths fall under a protected glob (§7.4 evaluator integrity). Matches on
    the full path and on any leading directory, so 'tests' guards 'tests/foo/bar_test.py'."""
    if not protected:
        return []
    hits = []
    for path in sorted(changed):
        parts = path.split("/")
        prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
        for pat in protected:
            pat = pat.rstrip("/")
            if any(fnmatch(path, pat) or fnmatch(pfx, pat) for pfx in prefixes):
                hits.append(path)
                break
    return hits


def verify_passes(verify_cmd: str, cwd: str, fitness: str = "auto", timeout: int = 600) -> bool:
    """True if the project's own check currently passes fully. A cheap single run used to
    short-circuit a worker that has already solved the task (§42) — never the builder's word."""
    from drydock.ratchet import score_output
    try:
        r = subprocess.run(verify_cmd, cwd=cwd, shell=True, capture_output=True,
                           text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    passed, total = score_output((r.stdout or "") + (r.stderr or ""), fitness, r.returncode)
    return total > 0 and passed >= total


# ── default Worker (bounded agent.run) + Evaluator (project verifier) ─────────
def default_worker(task: dict, mission: dict, cwd: str, base_config: dict) -> WorkerResult:
    """Run one bounded task via the in-process agent loop (the disposable Worker, §7.3).

    Bounded per §42: the loop stops at `max_turns`, at a per-task WALL-CLOCK budget
    (`worker_time_budget_s`), or as soon as the verifier already passes — so a worker can't
    burn a contended model spinning after the work is done. Budgets/short-circuit are checked
    at turn boundaries; a stop is graceful (the tree keeps whatever the worker changed)."""
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
    mconf = mission.get("config") or {}
    budget_s = float(task.get("worker_time_budget_s") or mconf.get("worker_time_budget_s") or 0)
    deadline = (time.monotonic() + budget_s) if budget_s > 0 else None
    stop_check = task.get("_verify_passes")     # callable()->bool, injected by run_task
    check_every = 20.0
    last_check = 0.0
    stop_reason = ""
    try:
        for ev in agent_run(objective, state, cfg, system_prompt):
            if not isinstance(ev, TurnDone):
                continue
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                stop_reason = f"worker time budget ({int(budget_s)}s) reached"
                break
            if stop_check is not None and (now - last_check) >= check_every:
                last_check = now
                try:
                    if stop_check():
                        stop_reason = "verifier already passing — stopping early"
                        break
                except Exception:  # noqa: BLE001 — an early-stop probe never crashes the worker
                    pass
    except Exception as e:  # noqa: BLE001 — a worker crash is contained by the loop (§32)
        return WorkerResult(ok=False, error=f"{type(e).__name__}: {e}")
    summ = ""
    for msg in reversed(getattr(state, "messages", []) or []):
        if isinstance(msg, dict) and msg.get("role") == "assistant" and (msg.get("content") or "").strip():
            summ = str(msg["content"]).strip()[:2000]
            break
    if stop_reason:
        summ = f"[{stop_reason}] {summ}".strip()
    return WorkerResult(ok=True, summary=summ,
                        in_tokens=int(getattr(state, "total_input_tokens", 0) or 0),
                        out_tokens=int(getattr(state, "total_output_tokens", 0) or 0))


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def make_verifier_evaluator(verify_cmd: str, fitness: str = "auto", timeout: int = 1800,
                            samples: int = 1, noise_band: float = 0.0) -> EvaluatorFn:
    """Deterministic Evaluator (§7.4): run the project's own check, score it, KEEP only if it
    beats the metric by more than the noise band (§9/§20). Never the builder's word.

    Noise policy: run the check `samples` times and take the MEDIAN, so one flaky run can't
    flip the decision; KEEP only if median rose by more than `noise_band` points. Within the
    band = no meaningful change → not accepted (the code is reverted to the known-good tree),
    so a variance-driven blip is never locked in as a 'win'. The baseline is measured by the
    same evaluator, so baseline and experiments use identical sampling."""
    from drydock.ratchet import score_output
    n = max(1, int(samples))

    def evaluate(task: dict, mission: dict, cwd: str, before: float) -> Evaluation:
        runs: list[tuple[float, int, int]] = []
        for _ in range(n):
            try:
                r = subprocess.run(verify_cmd, cwd=cwd, shell=True, capture_output=True,
                                   text=True, timeout=timeout)
            except (OSError, subprocess.SubprocessError):
                return Evaluation(accept=False, metric_before=before,
                                  reason="verifier failed to run")
            passed, total = score_output((r.stdout or "") + (r.stderr or ""), fitness, r.returncode)
            score = 100.0 * passed / total if total else (100.0 if r.returncode == 0 else 0.0)
            runs.append((score, passed, total))
        metrics = [x[0] for x in runs]
        after = _median(metrics)
        spread = max(metrics) - min(metrics)
        # representative pass count = the run nearest the median (for the 'all passed' flag)
        rep = min(runs, key=lambda x: abs(x[0] - after))
        accept = after > before + noise_band     # must clear the noise band to count (§9/§20)
        band = f", band {noise_band:.1f}" if noise_band else ""
        spr = f", spread {spread:.1f}" if n > 1 else ""
        return Evaluation(accept=accept, metric_before=before, metric_after=after,
                          passed=bool(rep[2] and rep[1] >= rep[2]),
                          reason=f"metric {before:.1f}→{after:.1f} (median of {n}{spr}{band})")
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
             worker_id: str = "worker-1",
             checkpoint_fn: "Callable[[str], str]" = checkpoint,
             restore_fn: "Callable[[str, str], bool]" = restore) -> TaskOutcome | None:
    """Execute one leased task with checkpoint + KEEP/REVERT. None if the lease was lost.

    checkpoint_fn/restore_fn default to git (`repo` = a checkout dir), but are injectable so a
    non-git target can plug in its own snapshot mechanism — e.g. `docker commit` for a task that
    lives in a container (the tbench/ddt harness). `repo` is then just an opaque handle passed
    to those callables; an empty `repo` disables checkpointing."""
    mid = mission["id"]
    tid = task["id"]
    if not store.claim_task(tid, worker_id):
        return None
    before = mission.get("current_metric") or 0.0
    cp = checkpoint_fn(repo) if repo else ""
    store.event(mid, "task_started", task=tid, objective=task.get("objective", ""))
    # Context reconstruction (§13): surface known failed approaches so the worker doesn't
    # repeat them (§16). Key the lookup on the SPECIFIC target (task reason, e.g. the failing
    # check) when present — sharper than the boilerplate objective every task shares.
    target = task.get("reason", "") or task.get("objective", "")[:120]
    neg = store.similar_knowledge(mid, target, type=M.K_NEGATIVE, top=3)
    task = dict(task)
    task["negative_knowledge"] = [k["statement"] for k in neg]
    # §42 early-stop: let the worker check the project verifier so it can quit once it's done.
    verify_cmd = (mission.get("config") or {}).get("verify_cmd") or ""
    if verify_cmd:
        task["_verify_passes"] = lambda: verify_passes(verify_cmd, cwd)
    t0 = time.monotonic()
    wr = worker(task, mission, cwd, base_config or {})
    store.add_usage(mid, tokens=wr.in_tokens + wr.out_tokens, wall_s=time.monotonic() - t0)
    if not wr.ok:
        if repo and cp:
            restore_fn(repo, cp)
        store.complete_task(tid, M.T_FAILED, {"error": wr.error})
        return TaskOutcome(tid, accept=False, metric_after=before, reverted=True, error=wr.error)

    # What did this experiment touch? Used both for the integrity guard and to make the
    # KEEP/REVERT knowledge discriminating (§16) — must be read BEFORE any restore().
    changed = sorted(changed_paths(repo, cp)) if (repo and cp) else []
    # Evaluator integrity (§7.4): a worker must not edit the measurement apparatus. If this
    # experiment touched a protected path the metric is untrusted — reject WITHOUT running the
    # (possibly rigged) verifier, revert, and remember the tamper as a failed approach (§16).
    protected = (mission.get("config") or {}).get("protected_paths") or []
    tamper = tampered_paths(changed, protected)
    if tamper:
        if repo and cp:
            restore_fn(repo, cp)
        store.add_usage(mid, experiments=1)
        store.complete_task(tid, M.T_FAILED,
                            {"summary": wr.summary, "decision": "REVERT", "reverted": True,
                             "tampered": tamper})
        store.event(mid, "tamper", task=tid, paths=tamper[:20])
        store.event(mid, "experiment", task=tid, decision="REVERT", metric_before=before,
                    metric_after=before, reason=f"protected paths modified: {tamper[:5]}")
        store.add_knowledge(mid, M.K_NEGATIVE,
                            f"REJECTED (measurement tamper): {task.get('objective', '')}. Modified "
                            f"protected measurement paths {tamper[:5]} — result untrusted.",
                            confidence=0.9, sources=[tid])
        return TaskOutcome(tid, accept=False, metric_after=before, reverted=True,
                           error=f"protected paths modified: {tamper[:5]}")

    ev = evaluator(task, mission, cwd, before)
    store.add_usage(mid, experiments=1)
    if ev.accept:
        end = checkpoint_fn(repo) if repo else ""
        store.set_metric(mid, ev.metric_after)
        store.complete_task(tid, M.T_COMPLETED,
                            {"summary": wr.summary, "metric": ev.metric_after, "decision": "KEEP",
                             "commit": end})
        store.event(mid, "experiment", task=tid, decision="KEEP",
                    metric_before=ev.metric_before, metric_after=ev.metric_after, commit=end)
        files = f" files={changed[:6]}" if changed else ""
        store.add_knowledge(mid, M.K_FINDING,
                            f"KEPT ({ev.metric_before:.1f}→{ev.metric_after:.1f}) {target}{files}: "
                            f"{(wr.summary or '')[:220]}", confidence=0.7, sources=[tid])
    else:
        if repo and cp:
            restore_fn(repo, cp)      # auto-revert the regression (§19)
        store.complete_task(tid, M.T_COMPLETED,
                            {"summary": wr.summary, "decision": "REVERT", "reverted": True})
        # negative knowledge stays in history even though the code is reverted (§16/§19)
        store.event(mid, "experiment", task=tid, decision="REVERT",
                    metric_before=ev.metric_before, metric_after=ev.metric_after, reason=ev.reason)
        # Discriminating negative knowledge (§16/AT-8): key on the specific target + the files
        # the worker actually changed + what it tried — NOT the boilerplate objective every task
        # shares, which made all reverts look alike and defeated similarity retrieval.
        files = f" files={changed[:6]}" if changed else ""
        detail = " ".join(x for x in (wr.summary, ev.reason) if x)[:240]
        store.add_knowledge(mid, M.K_NEGATIVE,
                            f"REVERTED ({ev.metric_before:.1f}→{ev.metric_after:.1f}, no gain) "
                            f"{target}{files}: {detail}", confidence=0.6, sources=[tid])
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


def strategic_review_due(store: M.MissionStore, mission_id: str, *,
                         every_tasks: int = 10, every_secs: float = 3600.0) -> str:
    """Return a trigger reason if a strategic review is due (§24), else "". Triggers off the
    durable event log / review table, so it is correct across resume. every_tasks<=0 or
    every_secs<=0 disables that trigger."""
    since = store.last_review_ts(mission_id)
    if every_tasks > 0 and store.experiments_since(mission_id, since) >= every_tasks:
        return f"{every_tasks} experiments since last review"
    if every_secs > 0 and (time.time() - since) >= every_secs:
        return f"{int(every_secs // 60)} minutes since last review"
    return ""


def run_strategic_review(store: M.MissionStore, mission_id: str, *,
                         planner: Callable[[M.MissionStore, dict], int] | None = None,
                         reviewer: Callable[[M.MissionStore, dict, dict], dict] | None = None,
                         trigger: str = "") -> dict:
    """Reconsider strategy (§24). Deterministically answers the measurable review questions
    from durable state, records an immutable review + event, then refreshes the backlog via
    the planner. An optional model-backed `reviewer(store, mission, facts)->dict` may enrich
    the summary (model routing, §32) — never required for the loop to make progress."""
    m = store.get_mission(mission_id) or {}
    exps = [e for e in store.events(mission_id) if e["type"] == "experiment"]
    kept = sum(1 for e in exps if e.get("decision") == "KEEP")
    reverted = sum(1 for e in exps if e.get("decision") == "REVERT")
    completed = sum(1 for t in store.tasks(mission_id) if t["status"] == M.T_COMPLETED)
    baseline = (m.get("baseline") or {}).get("metric")
    current, best = m.get("current_metric"), m.get("best_metric")
    failed = [k["statement"] for k in store.knowledge(mission_id, M.K_NEGATIVE)][-5:]
    gained = current is not None and baseline is not None and current > baseline
    if reverted and reverted >= 3 * max(kept, 1):
        limiting = "most experiments regress — current approaches are not working; change tactics"
    elif not gained:
        limiting = "no measurable gain over baseline yet"
    else:
        limiting = "progressing; keep pursuing the current line"
    facts = {
        "trigger": trigger, "objective": m.get("objective", ""),
        "target": (m.get("success_criteria") or {}).get("metric"),
        "baseline": baseline, "current": current, "best": best,
        "tasks_completed": completed, "experiments_kept": kept,
        "experiments_reverted": reverted, "failed_strategies": failed,
        "limiting_factor": limiting,
    }
    if reviewer is not None:
        try:
            facts.update(reviewer(store, m, dict(facts)) or {})
        except Exception:  # noqa: BLE001 — a review never crashes the mission (§7.4/§50)
            pass
    store.record_review(mission_id, facts)
    added = 0
    if planner is not None:
        try:
            added = planner(store, m) or 0
        except Exception:  # noqa: BLE001
            added = 0
    facts["backlog_added"] = added
    store.event(mission_id, "strategic_review", trigger=trigger, kept=kept,
                reverted=reverted, added=added, limiting=limiting)
    return facts


def escalate(store: M.MissionStore, mission_id: str, *, count: int, max_escalations: int,
             base_config: dict, planner: Callable[[M.MissionStore, dict], int] | None = None,
             critic: Callable[[M.MissionStore, dict, dict], str] | None = None) -> str:
    """Climb the escalation ladder (§23) when a worker stalls — advisory, never raises (§50).
    Returns the next disposition: 'continue' (try again with a changed strategy/model),
    'blocked' (task/mission out of ideas), or 'human' (mission-critical, needs a human).

    Deterministic subset of the ladder: L2 Critic analyses the stall (optional model hook,
    else a recorded finding), L3 Planner proposes an alternative strategy, L5 model routing
    swaps to `escalation_model` if configured, and — because stagnation must never be 'solved'
    by unlimited iterations (§23) — after `max_escalations` climbs or when no alternative work
    can be produced it stops: AWAITING_HUMAN if the mission is critical, else BLOCKED."""
    m = store.get_mission(mission_id) or {}
    cfg = m.get("config") or {}
    facts = {"objective": m.get("objective", ""), "escalation": count,
             "failed_strategies": [k["statement"] for k in
                                   store.knowledge(mission_id, M.K_NEGATIVE)][-5:]}
    # L2 — Critic: why are we stuck? (model hook optional; deterministic fallback below)
    note = ""
    if critic is not None:
        try:
            note = critic(store, m, dict(facts)) or ""
        except Exception:  # noqa: BLE001 — the critic never crashes the mission
            note = ""
    if not note:
        note = (f"stalled after repeated non-progress (escalation {count}); recent approaches "
                f"are not moving the metric — a different tactic is needed")
    store.add_knowledge(mission_id, M.K_ASSUMPTION, f"CRITIC: {note}", confidence=0.5)
    # L5 — model routing: switch to a stronger/different model for subsequent workers (§32)
    alt = str(cfg.get("escalation_model") or "")
    model_switched = False
    if alt and base_config.get("model") != alt:
        base_config["model"] = alt
        model_switched = True
    # L3 — Planner: create an alternative strategy
    added = 0
    if planner is not None:
        try:
            added = planner(store, m) or 0
        except Exception:  # noqa: BLE001
            added = 0
    store.event(mission_id, "escalation", level=count, note=note, backlog_added=added,
                model_switched=model_switched)
    # L6/L7/L8 — stop climbing: no unlimited iterations (§23)
    critical = bool(cfg.get("mission_critical"))
    if count >= max_escalations:
        return "human" if critical else "blocked"
    if added == 0 and not store.ready_tasks(mission_id):
        return "human" if critical else "blocked"
    return "continue"


def run_mission(store: M.MissionStore, mission_id: str, *, cwd: str, repo: str = "",
                worker: WorkerFn = default_worker, evaluator: EvaluatorFn | None = None,
                planner: Callable[[M.MissionStore, dict], int] | None = None,
                base_config: dict | None = None, worker_id: str = "worker-1",
                max_cycles: int = 10000, stagnation_limit: int = 5, max_escalations: int = 3,
                review_every_tasks: int = 10, review_every_secs: float = 3600.0,
                reviewer: Callable[[M.MissionStore, dict, dict], dict] | None = None,
                critic: Callable[[M.MissionStore, dict, dict], str] | None = None,
                checkpoint_fn: "Callable[[str], str]" = checkpoint,
                restore_fn: "Callable[[str, str], bool]" = restore,
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
    base_config = dict(base_config or {})   # own copy: model routing (§32) may mutate it
    no_progress = 0
    escalations = 0
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
        why_review = strategic_review_due(store, mission_id, every_tasks=review_every_tasks,
                                          every_secs=review_every_secs)
        if why_review:
            facts = run_strategic_review(store, mission_id, planner=planner,
                                         reviewer=reviewer, trigger=why_review)
            _ev("strategic_review", trigger=why_review, added=facts.get("backlog_added", 0))
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
                       base_config=base_config, worker_id=worker_id,
                       checkpoint_fn=checkpoint_fn, restore_fn=restore_fn)
        progressed = bool(out and out.accept)
        _ev("task_done", task=task["id"], accepted=progressed,
            metric=out.metric_after if out else None)
        no_progress = 0 if progressed else no_progress + 1
        if no_progress >= stagnation_limit:
            store.event(mission_id, "stagnation", cycles=no_progress)
            _ev("stagnation", cycles=no_progress)
            escalations += 1
            disp = escalate(store, mission_id, count=escalations, max_escalations=max_escalations,
                            base_config=base_config, planner=planner, critic=critic)
            _ev("escalation", level=escalations, disposition=disp)
            if disp == "continue":
                no_progress = 0                   # a changed strategy earns a fresh window (§23)
                continue
            status = M.M_AWAITING_HUMAN if disp == "human" else M.M_BLOCKED
            store.set_status(mission_id, status)
            store.event(mission_id, "blocked" if disp != "human" else "awaiting_human",
                        escalations=escalations)
            _ev(disp, escalations=escalations)
            break
    m = store.get_mission(mission_id) or {}
    return RunSummary(mission_id=mission_id, status=m.get("status", "?"), cycles=cycles,
                      final_metric=m.get("current_metric"))

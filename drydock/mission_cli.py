"""`drydock mission` — CLI + planner + status/timeline views (Phase-1 slice 3).

Wires the durable store (mission.py) and controller loop (mission_run.py) into the user-facing
commands (§36): create / list / status / tasks / logs / experiments / knowledge / run / resume
/ pause / stop. A minimal Planner (§7.2) seeds the improvement loop and replans when the queue
empties, so a mission keeps iterating Observe→improve→measure→KEEP/REVERT until success /
budget / stagnation (§17/§41). Human-readable MISSION.md / CURRENT_STATE.md are generated views
over the canonical SQLite state (§40).
"""
from __future__ import annotations

import os
import time

from drydock import mission as M
from drydock import mission_run as R


# ── minimal planner (§7.2) ────────────────────────────────────────────────────
def _improve_task_text(objective: str, verify: str) -> str:
    return (f"Make ONE concrete, measurable improvement toward the mission objective: "
            f"{objective}\nInvestigate the current state, implement a single focused change, "
            f"and make sure the project's checks still pass. The change is scored by: "
            f"{verify or 'the project verifier'}. Keep the change minimal and reversible.")


def initial_plan(store: M.MissionStore, mission_id: str, objective: str, verify: str) -> int:
    store.set_status(mission_id, M.M_PLANNING)
    store.add_task(mission_id, _improve_task_text(objective, verify), reason="bootstrap",
                   priority=5)
    return 1


def iterate_planner(objective: str, verify: str):
    """Replanner: adds one improvement task when the queue empties (bounded by the mission's
    budget + stagnation detector, so it never loops forever, §30/§22)."""
    def plan(store: M.MissionStore, mission: dict) -> int:
        store.add_task(mission["id"], _improve_task_text(objective, verify), reason="iterate",
                       priority=5)
        return 1
    return plan


# ── views (§38/§39/§40) ───────────────────────────────────────────────────────
def _hms(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}h {(s % 3600) // 60}m"


def render_status(store: M.MissionStore, mission_id: str) -> str:
    m = store.get_mission(mission_id)
    if not m:
        return f"no such mission: {mission_id}"
    counts: dict[str, int] = {}
    for t in store.tasks(mission_id):
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    exps = [e for e in store.events(mission_id) if e["type"] == "experiment"]
    kept = sum(1 for e in exps if e.get("decision") == "KEEP")
    rev = sum(1 for e in exps if e.get("decision") == "REVERT")
    u = store.usage(mission_id)
    base = (m.get("baseline") or {})
    lines = [
        f"DRYDOCK MISSION {m['id']}",
        f"Status:    {m['status']}",
        f"Objective: {m['objective']}",
        f"Elapsed:   {_hms(time.time() - (m.get('created_at') or time.time()))}",
        f"Baseline:  {base.get('metric', '—')}   Current: {m.get('current_metric', '—')}   "
        f"Best: {m.get('best_metric', '—')}",
        f"Success:   {m.get('success_criteria') or {}}",
        ("Tasks:     " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
         if counts else "Tasks:     (none)"),
        f"Experiments: {len(exps)}  (kept {kept}, reverted {rev})",
        f"Usage:     tokens={u['tokens']}  experiments={u['experiments']}  failures={u['failures']}",
    ]
    revs = store.reviews(mission_id)
    since = store.last_review_ts(mission_id)
    to_go = max(0, 10 - store.experiments_since(mission_id, since))
    lines.append(f"Reviews:   {len(revs)}   next strategic review in {to_go} experiments")
    if revs:
        lines.append(f"Last review: {revs[-1]['summary'].get('limiting_factor', '')}")
    return "\n".join(lines)


def render_timeline(store: M.MissionStore, mission_id: str, limit: int = 40) -> str:
    out = []
    for e in store.events(mission_id)[-limit:]:
        ts = time.strftime("%H:%M:%S", time.localtime(e.get("ts", 0)))
        extra = {k: v for k, v in e.items() if k not in ("seq", "ts", "type")}
        out.append(f"{ts}  {e['type']}  {extra if extra else ''}".rstrip())
    return "\n".join(out) or "(no events)"


def write_views(store: M.MissionStore, mission_id: str, cwd: str) -> None:
    d = M.missions_dir(cwd) / mission_id
    try:
        d.mkdir(parents=True, exist_ok=True)
        m = store.get_mission(mission_id) or {}
        (d / "MISSION.md").write_text(
            f"# {mission_id}\n\n**Objective:** {m.get('objective', '')}\n\n"
            f"**Success:** {m.get('success_criteria')}\n\n**Budget:** {m.get('budget')}\n",
            encoding="utf-8")
        (d / "CURRENT_STATE.md").write_text(render_status(store, mission_id) + "\n\n## Timeline\n"
                                            + render_timeline(store, mission_id) + "\n",
                                            encoding="utf-8")
    except OSError:
        pass


# ── CLI (§36) ─────────────────────────────────────────────────────────────────
def _parse_budget(argv: list[str]) -> tuple[dict, dict, str, list[str], list[str]]:
    """Pull mission flags out of argv, returning
    (budget, success_criteria, verify, protected_paths, rest)."""
    budget: dict = {}
    success: dict = {}
    verify = ""
    protected: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else ""
        if a == "--time-budget" and nxt:
            try:
                budget["wall_time_hours"] = float(nxt.rstrip("hH"))
            except ValueError:
                pass
            i += 2
        elif a == "--target" and nxt:
            success["metric"] = nxt
            i += 2
        elif a == "--max-experiments" and nxt:
            budget["max_experiments"] = int(nxt) if nxt.isdigit() else 0
            i += 2
        elif a == "--verify" and nxt:
            verify = nxt
            i += 2
        elif a == "--protect" and nxt:            # measurement apparatus a worker must not edit
            protected.append(nxt)
            i += 2
        else:
            rest.append(a)
            i += 1
    return budget, success, verify, protected, rest


def _latest(cwd: str) -> str | None:
    ids = M.list_missions(cwd)
    return ids[-1] if ids else None


def run_cli(argv: list, config: dict | None = None) -> int:
    config = config or {}
    cwd = str(config.get("cwd") or os.getcwd())
    argv = list(argv or [])
    sub = argv[0] if argv else "help"
    rest = argv[1:]

    if sub == "create":
        budget, success, verify, protected, objparts = _parse_budget(rest)
        objective = " ".join(objparts).strip()
        if not objective:
            print('usage: drydock mission create "<objective>" [--target ">=70"] '
                  '[--verify CMD] [--protect GLOB]... [--time-budget 48h] [--max-experiments N]')
            return 1
        mconf = {"verify_cmd": verify, "model": config.get("model", ""),
                 "base_url": config.get("base_url", ""), "provider": config.get("provider", "vllm"),
                 "protected_paths": protected}
        mid, store = M.create_mission(cwd, objective, success_criteria=success or None,
                                      budget=budget or None, config=mconf)
        initial_plan(store, mid, objective, verify)
        write_views(store, mid, cwd)
        print(f"Mission {mid} created.\nObjective: {objective}")
        if success:
            print(f"Target:    {success['metric']}")
        if budget:
            print(f"Budget:    {budget}")
        print(f"\nStart it (unattended-friendly — run in tmux/nohup):\n  drydock mission run {mid}")
        return 0

    if sub == "list":
        for row in M.list_missions(cwd):
            print(row)
        return 0

    if sub in ("status", "tasks", "logs", "timeline", "experiments", "knowledge", "stop",
               "pause", "run", "resume"):
        mid = rest[0] if rest else _latest(cwd)
        if not mid:
            print("No missions yet. Create one: drydock mission create \"<objective>\"")
            return 1
        store = M.open_store(cwd, mid)
        if sub == "status":
            print(render_status(store, mid))
        elif sub in ("logs", "timeline"):
            print(render_timeline(store, mid, limit=200))
        elif sub == "tasks":
            for t in store.tasks(mid):
                print(f"[{t['status']:9}] {t['id']}  p{t['priority']}  {t['objective'][:80]}")
        elif sub == "experiments":
            for e in store.events(mid):
                if e["type"] == "experiment":
                    print(f"{e.get('decision')}  {e.get('metric_before')}→{e.get('metric_after')}"
                          f"  {e.get('reason', '')}")
        elif sub == "knowledge":
            items = store.knowledge(mid)
            if not items:
                print("(no knowledge recorded yet)")
            for k in items:
                src = ",".join(str(s) for s in (k.get("sources") or []))
                print(f"[{k['type']:15}] conf={k.get('confidence', 0):.2f}  {k['statement']}"
                      + (f"  «{src}»" if src else ""))
        elif sub in ("stop", "pause"):
            store.set_status(mid, M.M_CANCELLED if sub == "stop" else M.M_PAUSED)
            print(f"{mid} → {'CANCELLED' if sub == 'stop' else 'PAUSED'}")
        elif sub in ("run", "resume"):
            return _run(store, mid, cwd, config, resume=(sub == "resume"))
        return 0

    print("drydock mission <create|list|status|tasks|logs|experiments|run|resume|pause|stop>")
    return 0 if sub == "help" else 1


def _run(store: M.MissionStore, mid: str, cwd: str, config: dict, *, resume: bool) -> int:
    m = store.get_mission(mid)
    if not m:
        print(f"no such mission: {mid}")
        return 1
    if resume:
        n = store.reclaim_expired(mid)
        print(f"Resuming {mid} (reclaimed {n} expired lease(s)).")
    mconf = m.get("config") or {}
    verify = str(mconf.get("verify_cmd") or "")
    if not verify:
        try:
            from drydock.ratchet import detect_verifier
            found = detect_verifier(cwd)
            if found:
                verify = found[0]
        except Exception:  # noqa: BLE001
            verify = ""
    evaluator = R.make_verifier_evaluator(verify) if verify else None
    base_config = dict(config)
    if evaluator is not None:
        b = R.establish_baseline(store, mid, cwd, evaluator)
        if b is not None:
            print(f"   baseline: {b:.1f}")
    print(f"⚓ Mission {mid} running (worker + evaluator loop). Ctrl-C to stop.\n"
          f"   objective: {m['objective']}\n   verify: {verify or '(none — cannot score; will idle)'}\n")

    def on_event(kind: str, d: dict) -> None:
        if kind == "task_done":
            print(f"  · task {d.get('task')}: {'KEPT' if d.get('accepted') else 'reverted'} "
                  f"metric={d.get('metric')}")
        elif kind in ("completed", "stopped", "stagnation", "idle"):
            print(f"  ⚑ {kind}: {d}")
        write_views(store, mid, cwd)

    R.run_mission(store, mid, cwd=cwd, repo=cwd, evaluator=evaluator,
                  planner=iterate_planner(m["objective"], verify), base_config=base_config,
                  on_event=on_event)
    write_views(store, mid, cwd)
    print("\n" + render_status(store, mid))
    return 0

#!/usr/bin/env python3
"""eratchet ± --share — the matched-compute blackboard control (see PRE_REGISTRATION.md).

For each task, run the SAME evolutionary ratchet twice at identical generations / fanout /
servers — blind (share=False, the baseline) and coordinated (share=True) — and record, per
arm: solved?, generations-to-solve, and variants-run (the compute measure). One variable:
whether each generation's variants read prior generations' scored attempts. Because the
budget is identical, a share win shows up as either MORE solves or the SAME solve at FEWER
variants (the compute-efficiency win the prior predicts).

Runs on git-repo tasks (eratchet's native substrate: host worktrees + a verify command).
`adapt_tbench_task` lays a terminal-bench-2 task out in that form for the pure-python subset.

Usage:
  python eratchet_control.py --dry                 # plumbing smoke, fake runner, no model
  python eratchet_control.py --tasks-dir DIR --servers http://host:8000/v1 --generations 6
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass

sys.path.insert(0, "/data3/drydock-v3")
from drydock.eratchet import (  # noqa: E402
    ExecConfig,
    VariantOutcome,
    make_variant_runner,
    run_eratchet,
)


@dataclass
class ArmResult:
    task: str
    arm: str            # "blind" | "share"
    category: str       # "flatline" | "gradient"
    solved: bool
    generations: int
    variants: int       # total variant runs = the matched-compute measure
    best_passed: int
    best_total: int


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _classify(tests_file: str) -> str:
    """flatline = a single all-or-nothing check (no gradient); gradient = several."""
    try:
        n = sum(1 for ln in open(tests_file, encoding="utf-8") if ln.lstrip().startswith("def test"))
    except OSError:
        n = 1
    return "flatline" if n <= 1 else "gradient"


def adapt_tbench_task(task_dir: str, dest_root: str) -> dict | None:
    """Lay a terminal-bench-2 task out as a git-repo task: a fresh repo holding its plaintext
    tests/ + a GOAL from instruction.md, verified by pytest. Returns None if it doesn't adapt
    cleanly (missing tests) — the caller skips it. Deps are the caller's responsibility (only
    the pure-python subset runs on the host)."""
    name = os.path.basename(task_dir.rstrip("/"))
    tests_src = os.path.join(task_dir, "tests", "test_outputs.py")
    instr = os.path.join(task_dir, "instruction.md")
    if not os.path.exists(tests_src):
        return None
    repo = os.path.join(dest_root, name)
    os.makedirs(os.path.join(repo, "tests"), exist_ok=True)
    shutil.copy(tests_src, os.path.join(repo, "tests", "test_outputs.py"))
    goal = ""
    if os.path.exists(instr):
        goal = open(instr, encoding="utf-8").read().strip()
    open(os.path.join(repo, "GOAL.md"), "w", encoding="utf-8").write(goal or f"Solve: {name}")
    _git(["init", "-q"], repo)
    _git(["add", "-A"], repo)
    _git(["-c", "user.name=erx", "-c", "user.email=e@e", "commit", "-qm", "task"], repo)
    return {"name": name, "repo": repo, "goal": goal or f"Solve {name}",
            "verify": "python3 -m pytest tests/test_outputs.py -q",
            "category": _classify(os.path.join(repo, "tests", "test_outputs.py"))}


def run_arm(task: dict, share: bool, servers: list[str], generations: int, *,
            runner_factory=make_variant_runner, model: str = "gemma4",
            provider: str = "vllm") -> ArmResult:
    """Run one arm (blind or share) of eratchet on `task`, counting variants for matched
    compute. `runner_factory` is injectable so the harness is testable without a model."""
    variants = {"n": 0}

    def on_event(kind: str, d: dict) -> None:
        if kind == "variant_done":
            variants["n"] += 1

    cfg = ExecConfig(repo=task["repo"], goal=task["goal"], verify_cmd=task["verify"],
                     fitness="auto", model=model, provider=provider)
    res = run_eratchet(task["goal"], servers=servers, runner=runner_factory(cfg),
                       max_generations=generations, on_event=on_event, share=share)
    return ArmResult(task=task["name"], arm="share" if share else "blind",
                     category=task.get("category", "?"), solved=res.solved,
                     generations=res.generations, variants=variants["n"],
                     best_passed=res.best_passed, best_total=res.best_total)


def run_control(tasks: list[dict], servers: list[str], generations: int, out_csv: str, *,
                runner_factory=make_variant_runner) -> list[ArmResult]:
    """Paired per task: blind then share, identical budget. Appends rows to `out_csv`."""
    rows: list[ArmResult] = []
    new = not os.path.exists(out_csv)
    with open(out_csv, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[f.name for f in ArmResult.__dataclass_fields__.values()])
        if new:
            w.writeheader()
        for t in tasks:
            for share in (False, True):
                r = run_arm(t, share, servers, generations, runner_factory=runner_factory)
                w.writerow(asdict(r))
                fh.flush()
                rows.append(r)
                print(f"[{t['name']}/{r.arm}] solved={r.solved} gens={r.generations} "
                      f"variants={r.variants} best={r.best_passed}/{r.best_total}", flush=True)
    return rows


def resolve(rows: list[ArmResult]) -> dict:
    """p1: share solves >= blind, or same solves at fewer variants (compute win).
    p3: the share−blind solve edge is bigger on flatline than gradient."""
    by = {}
    for r in rows:
        by.setdefault(r.task, {})[r.arm] = r
    both = [(b["blind"], b["share"]) for b in by.values() if "blind" in b and "share" in b]
    blind_solved = sum(1 for b, _ in both if b.solved)
    share_solved = sum(1 for _, s in both if s.solved)
    # compute win: among tasks BOTH solve, did share use fewer variants?
    co = [(b, s) for b, s in both if b.solved and s.solved]
    fewer = sum(1 for b, s in co if s.variants < b.variants)
    def edge(cat):
        c = [(b, s) for b, s in both if b.category == cat]
        return (sum(s.solved for _, s in c) - sum(b.solved for b, _ in c)) if c else 0
    return {"tasks": len(both), "blind_solved": blind_solved, "share_solved": share_solved,
            "share_used_fewer_variants_on_shared_solves": f"{fewer}/{len(co)}",
            "p3_flatline_edge": edge("flatline"), "p3_gradient_edge": edge("gradient")}


# ── dry smoke: a fake runner, no model, just proves the harness plumbing ──────
def _fake_runner_factory(cfg):
    def runner(base, server, spec, xplan):
        # score improves once building on a base; enough to exercise both arms + counting
        p = 3 if base is None else 6
        return VariantOutcome(spec, server, p, 6, ref=f"r-{server}-{p}",
                              descriptor=frozenset({f"t{i}" for i in range(p)}),
                              messages=[{"role": "assistant", "content": f"tried {spec.get('mode')}"}])
    return runner


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="eratchet_control")
    ap.add_argument("--dry", action="store_true", help="fake runner, no model — plumbing smoke")
    ap.add_argument("--tasks-dir", default="", help="dir of tbench-2 task folders to adapt")
    ap.add_argument("--tasks", nargs="*", default=[], help="specific task names under --tasks-dir")
    ap.add_argument("--servers", default="http://localhost:8000/v1")
    ap.add_argument("--generations", type=int, default=6)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "eratchet_results.csv"))
    a = ap.parse_args(argv)
    servers = [s for s in a.servers.replace(",", " ").split() if s]

    dest = tempfile.mkdtemp(prefix="erxctl-tasks-")
    tasks: list[dict] = []
    if a.dry:
        # two synthetic git-repo tasks just to exercise the harness
        for name, cat in (("dry-flatline", "flatline"), ("dry-gradient", "gradient")):
            repo = os.path.join(dest, name)
            os.makedirs(repo)
            _git(["init", "-q"], repo)
            _git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                  "--allow-empty", "-m", "i"], repo)
            tasks.append({"name": name, "repo": repo, "goal": f"solve {name}",
                          "verify": "true", "category": cat})
    else:
        names = a.tasks or (sorted(os.listdir(a.tasks_dir)) if a.tasks_dir else [])
        for n in names:
            t = adapt_tbench_task(os.path.join(a.tasks_dir, n), dest)
            if t:
                tasks.append(t)
        if not tasks:
            print("no adaptable tasks found", file=sys.stderr)
            return 1

    rf = _fake_runner_factory if a.dry else make_variant_runner
    rows = run_control(tasks, servers, a.generations, a.out, runner_factory=rf)
    print("\nRESULT:", json.dumps(resolve(rows), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

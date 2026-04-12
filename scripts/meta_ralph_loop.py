#!/usr/bin/env python3
"""Meta-RALPH loop — escalating sample+retry with stuck detection.

Wraps ralph_loop's iteration logic with three escalation stages:

  Stage 1  Standard iteration (one sample per fix prompt).
  Stage 2  Best-of-3 sampling when progress stalls. Each sample runs
           from a clean snapshot of the last good state; fastest winner
           wins (first sample that improves tests is kept, others
           discarded).
  Stage 3  Stuck mode: inject a system note telling the TUI to:
           - web_search the specific error
           - consult worked_examples/ for the relevant pattern
           - try a fundamentally different approach, not another rewrite

Key difference from ralph_loop: when a fix iteration doesn't improve
tests, we don't just roll back — we respawn the drydock TUI fresh and
try again. Each new session re-reads the PRD and functional_tests from
scratch, getting a different response trajectory (the model is
stochastic enough that a fresh session produces different code).

Usage:
    python3 scripts/meta_ralph_loop.py \\
        --cwd /data3/drydock_test_projects/406_mini_db \\
        --pkg mini_db \\
        --max-stages 3 \\
        --samples-per-stage 3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pexpect

DRYDOCK_BIN = "/home/bobef/miniforge3/envs/drydock/bin/drydock"
SESSION_ROOT = Path.home() / ".vibe" / "logs" / "session"
WORKED_EXAMPLES_DIR = Path("/data3/drydock/worked_examples")


# ── Reuse the SessionWatcher and helpers from ralph_loop ──
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ralph_loop import (  # type: ignore
    SessionWatcher, type_prompt, drain_pty,
    wait_for_prompt_landing, wait_for_completion,
    run_functional_tests, snapshot_package, restore_snapshot,
)


def find_worked_example(cwd: Path, pkg: str, failure_output: str) -> tuple[str, str] | None:
    """Find the best-matching worked example for the current task.

    Returns (file_path, summary) or None. Keyword-matches against the
    PRD text + last failure output.
    """
    lookup = WORKED_EXAMPLES_DIR / "lookup.json"
    if not lookup.exists():
        return None
    try:
        data = json.loads(lookup.read_text())
    except Exception:
        return None

    # Collect search corpus: PRD + failure output + package name
    corpus_parts = [pkg]
    for fn in ("PRD.md", "PRD.master.md"):
        p = cwd / fn
        if p.exists():
            corpus_parts.append(p.read_text(errors="ignore"))
    if failure_output:
        corpus_parts.append(failure_output)
    corpus = " ".join(corpus_parts).lower()

    best = None
    best_score = 0
    for entry in data.get("examples", []):
        score = sum(1 for kw in entry.get("keywords", [])
                    if kw.lower() in corpus)
        if score > best_score:
            best = entry
            best_score = score

    if not best or best_score < 2:
        return None

    fp = WORKED_EXAMPLES_DIR / best["file"]
    if not fp.exists():
        return None
    return str(fp), best.get("summary", "")


def spawn_drydock(cwd: Path, log_path: Path) -> pexpect.spawn:
    env = {**os.environ, "TERM": "xterm-256color", "COLUMNS": "120", "LINES": "30"}
    child = pexpect.spawn(DRYDOCK_BIN, encoding="utf-8", timeout=5,
                          maxread=100000, env=env, cwd=str(cwd))
    child.logfile_read = open(log_path, "w")
    try:
        child.expect([r">", r"Drydock", r"┌"], timeout=30)
    except Exception:
        pass
    time.sleep(3)
    try:
        if "Trust this folder" in (child.before or ""):
            child.send("\x1b[D")
            time.sleep(0.2)
            child.send("\r")
            time.sleep(2)
    except Exception:
        pass
    return child


def terminate_drydock(child: pexpect.spawn) -> None:
    try:
        child.sendcontrol("c")
        time.sleep(0.5)
        child.terminate(force=True)
    except Exception:
        pass


def run_single_session(cwd: Path, pkg: str, prompt: str,
                       max_wait: int = 1200,
                       idle_threshold: int = 180) -> tuple[int, int, str]:
    """Spawn drydock, send ONE prompt, wait for it to finish,
    return (test_pass, test_fail, output_tail)."""
    log_path = Path(f"/tmp/meta_ralph_{pkg}_{int(time.time())}.log")
    start_time = time.time()
    child = spawn_drydock(cwd, log_path)
    watcher = SessionWatcher(cwd, since=start_time)

    try:
        type_prompt(child, prompt)

        # Wait for session to appear
        for i in range(300):
            drain_pty(child, 1.0)
            if watcher.find_session():
                break
            if i > 0 and i % 60 == 0:
                time.sleep(2)
                type_prompt(child, prompt)
        if not watcher.find_session():
            return 0, 999, "session never appeared"

        # Wait for prompt to land
        if not wait_for_prompt_landing(child, watcher, 0, timeout=90):
            time.sleep(5)
            type_prompt(child, prompt)
            if not wait_for_prompt_landing(child, watcher, 0, timeout=90):
                return 0, 999, "prompt did not land"

        # Wait for drydock to finish responding
        wait_for_completion(child, watcher, max_wait=max_wait,
                           idle_threshold=idle_threshold)
    finally:
        terminate_drydock(child)

    p, f, out = run_functional_tests(cwd)
    return p, f, out


def best_of_n(cwd: Path, pkg: str, base_prompt: str, n: int,
              current_best_pass: int, current_best_fail: int,
              current_best_out: str,
              snap: Path) -> tuple[int, int, str, Path]:
    """Try the prompt N times, keeping the best result.

    Between samples, restore the package from `snap`. If any sample
    strictly improves test_pass count, return immediately (fastest win).

    Returns (best_pass, best_fail, best_output, best_snap). If no sample
    improves, returns the input values unchanged (callers can still
    distinguish 'no progress' from 'all tests pass').
    """
    for i in range(n):
        print(f"    [sample {i+1}/{n}]")
        # Restore package from snapshot so each sample starts clean
        restore_snapshot(cwd, pkg, snap)

        p, f, out = run_single_session(cwd, pkg, base_prompt,
                                        max_wait=1200,
                                        idle_threshold=180)
        print(f"      → {p}/{p+f} tests pass")

        if p > current_best_pass:
            # Win! Snapshot this state and return immediately.
            new_snap = snapshot_package(cwd, pkg)
            print(f"      [new best: {p}, keeping]")
            return p, f, out, new_snap

    # No sample beat current best — restore snap and return UNCHANGED
    restore_snapshot(cwd, pkg, snap)
    return current_best_pass, current_best_fail, current_best_out, snap


def make_stuck_prompt(pkg: str, cwd: Path, failure_output: str) -> str:
    """Build the stage-3 'stuck' prompt with web_search + worked-example hints."""
    # Extract FAIL lines
    fail_lines = [ln for ln in (failure_output or "").split("\n")
                  if ln.startswith("FAIL:")][:5]
    fail_text = "\n".join(fail_lines)

    # Look up a worked example
    example_path = None
    example_summary = ""
    matched = find_worked_example(cwd, pkg, failure_output)
    if matched:
        example_path, example_summary = matched

    prompt = (
        f"You've tried fixing the failures multiple times without progress. "
        f"Current failures:\n\n{fail_text}\n\n"
        f"DO SOMETHING DIFFERENT. Ideas:\n"
        f"- Use `web_search` to look up the exact error message or the "
        f"technique you're trying (e.g. 'python sql tokenizer example').\n"
        f"- Use `web_fetch` to read a Stack Overflow answer or docs page.\n"
        f"- Read a DIFFERENT source file than the one you've been rewriting — "
        f"the bug may be upstream.\n"
    )
    if example_path:
        prompt += (
            f"\nA WORKED EXAMPLE is available at {example_path}\n"
            f"Summary: {example_summary}\n"
            f"Read it with read_file and use the STRUCTURE (not the exact "
            f"code) to guide your fix.\n"
        )
    prompt += (
        f"\nThen apply ONE targeted search_replace to fix ONE failure at a "
        f"time. Do NOT rewrite whole files. Do NOT ask me for confirmation."
    )
    return prompt


def meta_ralph(cwd: Path, pkg: str, max_stages: int = 3,
               samples_per_stage: int = 3) -> dict:
    """Main meta-RALPH loop."""
    print(f"\n{'='*60}")
    print(f"  META-RALPH: {pkg}")
    print(f"  cwd: {cwd}")
    print(f"  max stages: {max_stages}, samples/stage: {samples_per_stage}")
    print(f"{'='*60}\n")

    # Restore PRD.md
    master = cwd / "PRD.master.md"
    if master.exists():
        shutil.copy2(master, cwd / "PRD.md")

    start_time = time.time()
    stages_log = []

    # ── STAGE 1: Initial build (single sample, standard prompt) ──
    print(f"─── STAGE 1: Initial build ───")
    pkg_init = cwd / pkg / "__init__.py"
    if pkg_init.exists():
        initial_prompt = (
            f"Look at the existing {pkg}/ directory and functional_tests.sh. "
            f"Fix any failing tests with targeted search_replace patches."
        )
    else:
        initial_prompt = (
            f"Read PRD.md AND functional_tests.sh. Build the {pkg} package. "
            f"Your CLI must conform to functional_tests.sh exactly. "
            f"Create ALL files from the PRD. Write __init__.py, cli.py, "
            f"__main__.py FIRST."
        )

    p, f, out = run_single_session(cwd, pkg, initial_prompt,
                                    max_wait=1500, idle_threshold=180)
    print(f"  Stage 1 result: {p}/{p+f} tests pass")
    best_pass = p
    best_fail = f
    best_out = out
    best_snap = snapshot_package(cwd, pkg)
    stages_log.append({"stage": 1, "pass": p, "fail": f, "strategy": "single_build"})

    # Early exit if perfect
    if best_fail == 0 and best_pass > 0:
        print(f"\n  ✓ All tests pass! Done at stage 1.")
        return _report(cwd, pkg, start_time, stages_log, best_pass, best_fail)

    # ── STAGE 2: best-of-N sampling ──
    if max_stages >= 2:
        print(f"\n─── STAGE 2: best-of-{samples_per_stage} sampling ───")
        fail_lines = "\n".join(ln for ln in out.split("\n")
                                if ln.startswith("FAIL:"))[:1500]
        fix_prompt = (
            f"Tests are failing:\n{fail_lines}\n\n"
            f"Make TARGETED search_replace patches. Do not rewrite working files. "
            f"Do not break currently-passing tests. Run bash functional_tests.sh "
            f"when you think you're done."
        )
        p, f, out, best_snap = best_of_n(
            cwd, pkg, fix_prompt, samples_per_stage,
            best_pass, best_fail, best_out, best_snap,
        )
        print(f"  Stage 2 result: {p}/{p+f} tests pass")
        best_pass, best_fail, best_out = p, f, out
        stages_log.append({
            "stage": 2, "pass": p, "fail": f,
            "strategy": f"best_of_{samples_per_stage}",
        })
        if best_fail == 0 and best_pass > 0:
            print(f"\n  ✓ All tests pass! Done at stage 2.")
            return _report(cwd, pkg, start_time, stages_log, best_pass, best_fail)

    # ── STAGE 3: stuck mode with web_search + worked example ──
    if max_stages >= 3:
        print(f"\n─── STAGE 3: stuck mode (web_search + worked example) ───")
        stuck_prompt = make_stuck_prompt(pkg, cwd, best_out)
        # One sample at a time; don't rollback — the stuck mode is a
        # last-ditch effort, any improvement is worth keeping.
        p, f, out = run_single_session(cwd, pkg, stuck_prompt,
                                        max_wait=1800, idle_threshold=240)
        print(f"  Stage 3 result: {p}/{p+f} tests pass")
        if p > best_pass:
            best_pass, best_fail, best_out = p, f, out
            best_snap = snapshot_package(cwd, pkg)
        else:
            restore_snapshot(cwd, pkg, best_snap)
        stages_log.append({
            "stage": 3, "pass": p, "fail": f, "strategy": "stuck_mode",
        })

    return _report(cwd, pkg, start_time, stages_log, best_pass, best_fail)


def _report(cwd: Path, pkg: str, start_time: float, stages_log: list,
            best_pass: int, best_fail: int) -> dict:
    total = int(time.time() - start_time)
    print(f"\n{'='*60}")
    print(f"  META-RALPH RESULTS: {pkg}")
    print(f"  Total time: {total}s ({total/60:.1f}m)")
    for s in stages_log:
        print(f"  stage {s['stage']}: {s['pass']}/{s['pass']+s['fail']} "
              f"({s['strategy']})")
    print(f"\n  FINAL: {best_pass}/{best_pass+best_fail} tests pass")
    print(f"{'='*60}\n")

    report = {
        "pkg": pkg,
        "total_seconds": total,
        "stages": stages_log,
        "final_pass": best_pass,
        "final_fail": best_fail,
    }
    out_path = Path(f"/tmp/meta_ralph_{pkg}_results.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"  Report: {out_path}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--pkg", required=True)
    parser.add_argument("--max-stages", type=int, default=3)
    parser.add_argument("--samples-per-stage", type=int, default=3)
    args = parser.parse_args()
    report = meta_ralph(
        Path(args.cwd).resolve(), args.pkg,
        args.max_stages, args.samples_per_stage,
    )
    return 0 if report["final_fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

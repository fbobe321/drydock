#!/usr/bin/env python3
"""Mega-RALPH loop — for 1h / 3h / 6h / 12h PRDs.

Differences from meta_ralph_loop:

  1. Long startup tolerance — 1800s wait for first session (vs 300s in
     comprehensive_loop), so heavy planning phases don't get aborted.
  2. Time-based stages rather than fixed count — keeps iterating until
     either 100% tests pass, total budget exhausted, or N consecutive
     no-progress iterations.
  3. Snapshots to a LOCAL GIT REPO inside the PRD directory after each
     improvement — so multi-session continuation is possible and
     partial progress is never lost to rollback.
  4. Per-interval pass-rate checkpointing — records (elapsed, pass, total)
     every 15 min to visualize a progress curve.
  5. Engages stuck_mode earlier (20 min no progress) and surfaces
     worked-examples + web_search hints more aggressively.

Usage:
    python3 scripts/mega_loop.py \\
        --cwd /data3/drydock_test_projects/mega/101_web_framework \\
        --pkg web_fw \\
        --budget-minutes 60 \\
        --no-progress-minutes 20

A `progress.json` file is written in the PRD directory with:
  { "checkpoints": [{"elapsed_s": 900, "pass": 5, "total": 25}, ...],
    "sessions":   [{"session_id": "...", "start": "...", "end": "...",
                    "tests_start": {pass, total},
                    "tests_end":   {pass, total}}],
    "final": {"pass": N, "total": M} }
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
from datetime import datetime, timezone
from pathlib import Path

import pexpect

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ralph_loop import (  # type: ignore
    SessionWatcher, drain_pty, run_functional_tests, snapshot_package,
    restore_snapshot, wait_for_prompt_landing, type_prompt,
    wait_for_completion,
)
from meta_ralph_loop import (  # type: ignore
    spawn_drydock, terminate_drydock, extract_root_errors,
    find_worked_example, make_stuck_prompt,
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_git_init_if_needed(cwd: Path, pkg: str) -> None:
    """Ensure pkg dir is a git repo so each improvement is committed.

    If cwd/pkg is not a directory (e.g. port tasks where the 'package' is
    actually a compiled binary), fall back to using cwd itself.
    """
    pkg_dir = cwd / pkg
    if not pkg_dir.exists() or not pkg_dir.is_dir():
        pkg_dir = cwd
    if (pkg_dir / ".git").exists():
        return
    try:
        subprocess.run(["git", "init"], cwd=pkg_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "drydock@local"],
                       cwd=pkg_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "drydock"],
                       cwd=pkg_dir, capture_output=True, check=True)
    except subprocess.CalledProcessError:
        pass


def git_commit_progress(cwd: Path, pkg: str, msg: str) -> None:
    pkg_dir = cwd / pkg
    if not pkg_dir.exists() or not pkg_dir.is_dir():
        pkg_dir = cwd
    if not (pkg_dir / ".git").exists():
        return
    try:
        subprocess.run(["git", "add", "-A"], cwd=pkg_dir, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", msg, "--allow-empty", "--no-gpg-sign"],
            cwd=pkg_dir, capture_output=True,
        )
    except subprocess.CalledProcessError:
        pass


def load_progress(cwd: Path) -> dict:
    p = cwd / "progress.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"checkpoints": [], "sessions": [], "final": None}


def save_progress(cwd: Path, data: dict) -> None:
    (cwd / "progress.json").write_text(json.dumps(data, indent=2))


def build_initial_prompt(pkg: str, cwd: Path) -> str:
    """Prompt for the first session of a brand-new build."""
    prd = cwd / "PRD.md"
    if not prd.exists():
        prd = cwd / "PRD.master.md"
    prd_name = prd.name if prd.exists() else "PRD.md"
    return (
        f"Read {prd_name} completely. Then build the `{pkg}` package to "
        f"satisfy EVERY feature it describes. Run `bash functional_tests.sh` "
        f"periodically to check progress. DO NOT stop after --help works — "
        f"the functional tests verify every feature. Iterate until 100% "
        f"pass or you've genuinely run out of ideas. "
        f"Commit your progress with `bash` running "
        f"`git -C {pkg} add -A && git -C {pkg} commit -m \"stage N\" --no-gpg-sign` "
        f"every time the pass-rate improves."
    )


def build_continue_prompt(pkg: str, cwd: Path, last_pass: int, total: int) -> str:
    """Prompt for a subsequent session that continues from last state."""
    return (
        f"You're continuing a partially-built `{pkg}` package. Current "
        f"functional_tests.sh status: {last_pass}/{total} passing. "
        f"Read the PRD, run `bash functional_tests.sh` to see which "
        f"specific tests fail, and FIX those. Don't rewrite files that "
        f"work — only touch what's needed to move failing tests to passing. "
        f"Commit with git after each improvement."
    )


def run_session(
    cwd: Path, pkg: str, prompt: str,
    session_budget_s: int,
    progress: dict,
    watcher_pass: int | None = None,
) -> tuple[int, int, str, str]:
    """Run ONE drydock session with the given prompt.

    Returns (pass, fail, output_text, session_id).
    """
    start_ts = utcnow()
    log_path = Path(f"/tmp/mega_{pkg}_{int(time.time())}.tui.log")
    child = spawn_drydock(cwd, log_path)
    watcher = SessionWatcher(cwd=cwd, since=time.time() - 2)

    # Drydock creates the session dir ONLY after receiving input.
    # Type the prompt FIRST, then wait for the session dir to appear.
    print(f"  [session] typing prompt ({len(prompt)} chars)...", flush=True)
    ucount = 0  # fresh TUI has no user messages yet
    type_prompt(child, prompt)

    # Now wait for the session dir (up to 600s — GPU may be busy).
    print(f"  [session] waiting for drydock session (max 600s)...", flush=True)
    session_ready = False
    for i in range(600):
        drain_pty(child, 1.0)
        if watcher.find_session():
            session_ready = True
            print(f"  [session] found: {watcher.session_dir.name}", flush=True)
            break
        if i > 0 and i % 120 == 0:
            # Retype after 2 min of silence
            print(f"  [session] retyping at {i}s", flush=True)
            try:
                type_prompt(child, prompt)
            except Exception:
                pass
    if not session_ready:
        print(f"  [session] NOT FOUND after 600s — aborting session", flush=True)
        terminate_drydock(child)
        return 0, 0, "", ""

    session_id = watcher.session_dir.name
    if not wait_for_prompt_landing(child, watcher, ucount, timeout=240):
        print(f"  [prompt] didn't land — retrying once", flush=True)
        time.sleep(20)
        type_prompt(child, prompt)
        wait_for_prompt_landing(child, watcher, ucount, timeout=240)

    print(f"  [run] prompt landed, budget={session_budget_s}s, 5m idle threshold", flush=True)
    status = wait_for_completion(child, watcher,
                                 max_wait=session_budget_s,
                                 idle_threshold=300)
    elapsed = int(time.time() - watcher.since)

    p, f, _ = run_functional_tests(cwd)
    print(f"  [result] status={status}, tests={p}/{p+f}, elapsed={elapsed}s", flush=True)

    terminate_drydock(child)
    return p, f, "", session_id


def mega_loop(cwd: Path, pkg: str,
              budget_minutes: int, no_progress_minutes: int) -> dict:
    budget_s = budget_minutes * 60
    no_progress_s = no_progress_minutes * 60
    start = time.time()
    progress = load_progress(cwd)

    local_git_init_if_needed(cwd, pkg)

    best_pass, best_fail, _ = run_functional_tests(cwd)
    total = best_pass + best_fail
    print(f"=== MEGA LOOP: {pkg} ===")
    print(f"Budget: {budget_minutes} minutes ({budget_s}s)")
    print(f"Initial tests: {best_pass}/{total}")
    print(f"cwd: {cwd}")

    last_improvement = start
    session_n = 0

    while time.time() - start < budget_s:
        elapsed = int(time.time() - start)
        session_n += 1

        # Log checkpoint every 15 min
        if not progress["checkpoints"] or (
            elapsed - progress["checkpoints"][-1]["elapsed_s"] >= 900
        ):
            progress["checkpoints"].append({
                "elapsed_s": elapsed,
                "pass": best_pass, "total": total,
                "ts": utcnow(),
            })
            save_progress(cwd, progress)

        # Exit if already clean
        if best_fail == 0 and total > 0:
            print(f"ALL TESTS PASS at {elapsed}s — done")
            break

        # Choose prompt
        if session_n == 1 and best_pass == 0 and total == 0:
            prompt = build_initial_prompt(pkg, cwd)
        elif time.time() - last_improvement > no_progress_s:
            # Stuck — inject stuck-mode prompt
            print(f"[stuck] {no_progress_minutes}m without progress, escalating")
            _, _, out = run_single_failure_read(cwd)
            prompt = make_stuck_prompt(pkg, cwd, out)
        else:
            prompt = build_continue_prompt(pkg, cwd, best_pass, total)

        print(f"\n─── Session {session_n} at {elapsed}s ({best_pass}/{total}) ───")

        remaining = budget_s - elapsed
        session_budget = min(remaining, 3600)  # max 1h per session
        p, f, _, sid = run_session(cwd, pkg, prompt, session_budget, progress)

        tot = p + f
        if p > best_pass:
            best_pass, best_fail, total = p, f, tot
            last_improvement = time.time()
            git_commit_progress(cwd, pkg, f"session {session_n}: {p}/{tot}")
            print(f"  [IMPROVED] → {p}/{tot} committed")
        else:
            print(f"  [no improvement] {p}/{tot}")

        progress["sessions"].append({
            "n": session_n,
            "session_id": sid,
            "ts": utcnow(),
            "result_pass": p, "result_total": tot,
        })
        save_progress(cwd, progress)

        if best_pass == total and total > 0:
            print(f"ALL TESTS PASS — done after session {session_n}")
            break

        # Small cooldown
        time.sleep(5)

    progress["final"] = {"pass": best_pass, "total": total,
                         "elapsed_s": int(time.time() - start)}
    save_progress(cwd, progress)
    print(f"\n=== MEGA LOOP DONE: {best_pass}/{total} in "
          f"{int((time.time()-start)/60)} min ===")
    return progress


def run_single_failure_read(cwd: Path) -> tuple[int, int, str]:
    """Run functional tests, capturing the full output for stuck-mode."""
    try:
        res = subprocess.run(
            ["bash", "functional_tests.sh"],
            cwd=cwd, capture_output=True, text=True, timeout=60,
        )
        out = res.stdout + res.stderr
        m = re.search(r"RESULT: (\d+) passed, (\d+) failed", out)
        if m:
            return int(m.group(1)), int(m.group(2)), out
    except Exception:
        pass
    return 0, 0, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--pkg", required=True)
    ap.add_argument("--budget-minutes", type=int, default=60)
    ap.add_argument("--no-progress-minutes", type=int, default=20)
    args = ap.parse_args()
    cwd = Path(args.cwd).resolve()
    mega_loop(cwd, args.pkg, args.budget_minutes, args.no_progress_minutes)
    return 0


if __name__ == "__main__":
    sys.exit(main())

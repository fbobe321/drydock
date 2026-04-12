#!/usr/bin/env python3
"""Comprehensive simulated-user loop — exercises ALL real-world coding skills.

Unlike ralph_loop (just build-test-fix), this walks the TUI through 9 phases
that mirror what a human engineer actually does:

  1. Requirements Gathering and Planning
  2. Writing New Code (Implementation)
  3. Testing Code (unit tests, not just functional)
  4. Code Reviews
  5. Debugging and Troubleshooting
  6. Maintaining Existing Codebases (refactoring)
  7. Version Control Management
  8. Documentation
  9. Optimization

Each phase sends a prompt, waits for completion, optionally runs checks.

Usage:
  python3 scripts/comprehensive_loop.py \\
      --cwd /data3/drydock_test_projects/408_lang_interp \\
      --pkg lang_interp
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


# ── Phase definitions ────────────────────────────────────────────────────
# Each phase: (name, prompt_template, max_wait_seconds, validation_fn)
# {pkg} is substituted with the package name.

PHASES = [
    (
        "1_plan",
        "PHASE 1 — Requirements Gathering and Planning. "
        "Read PRD.md carefully. Then create a file called PLAN.md that "
        "contains: (a) the list of files you need to create with a brief "
        "purpose for each, (b) the key algorithms or data structures, "
        "(c) edge cases to handle, (d) your testing strategy. Do NOT "
        "write any code yet — only PLAN.md.",
        600,
        lambda cwd, pkg: (cwd / "PLAN.md").exists(),
    ),
    (
        "2_build",
        "PHASE 2 — Writing New Code (Implementation). "
        "Now build the {pkg} package per PRD.md and your PLAN.md. Create "
        "all files under {pkg}/. Write __init__.py, cli.py, __main__.py "
        "FIRST so the package is importable, then add the module files. "
        "Do NOT stop until every file from PLAN.md is written.",
        1500,
        lambda cwd, pkg: (cwd / pkg / "__main__.py").exists(),
    ),
    (
        "3_test",
        "PHASE 3 — Testing Code. Create a tests/ directory. Write pytest-"
        "style unit tests in tests/test_{pkg}.py that cover the main "
        "public functions of your package. Aim for at least 5 test "
        "functions. Run: `python3 -m pytest tests/` and confirm they pass. "
        "If you can't run pytest, write them anyway as Python assertions.",
        900,
        lambda cwd, pkg: (cwd / "tests").exists() and any((cwd / "tests").iterdir()),
    ),
    (
        "4_review",
        "PHASE 4 — Code Review. Read each file in {pkg}/ and identify: "
        "(a) any code smells (duplication, long functions, unclear names), "
        "(b) potential bugs or edge cases not handled, (c) places where "
        "error handling is missing. Write your findings to REVIEW.md "
        "with file:line references where applicable.",
        600,
        lambda cwd, pkg: (cwd / "REVIEW.md").exists(),
    ),
    (
        "5_debug",
        "PHASE 5 — Debugging and Troubleshooting. Run `bash functional_tests.sh` "
        "from this directory and look at any failures. For EACH failing test: "
        "(a) read the failure message, (b) identify which source file is "
        "responsible, (c) diagnose the root cause, (d) fix it with search_replace. "
        "Then run functional_tests.sh again to verify the fix.",
        1200,
        lambda cwd, pkg: True,  # validation is the functional test score
    ),
    (
        "6_refactor",
        "PHASE 6 — Maintaining Existing Codebases. Look at the LARGEST file "
        "in {pkg}/. Refactor it to: (a) extract helper functions where you "
        "see duplication, (b) improve variable and function names, (c) add "
        "type hints to public functions. Do NOT change behavior — ALL "
        "existing tests (functional_tests.sh AND your unit tests) must "
        "still pass after refactoring.",
        900,
        lambda cwd, pkg: True,
    ),
    (
        "7_git",
        "PHASE 7 — Version Control Management. Run these bash commands: "
        "(1) `git init` if not already a repo, (2) `git add .`, (3) "
        "`git -c user.email=test@test.com -c user.name=Test commit -m 'Initial {pkg} implementation'`. "
        "Then create a feature branch: `git checkout -b feature/docs`. "
        "Confirm with `git log --oneline` and `git branch`.",
        300,
        lambda cwd, pkg: (cwd / ".git").exists(),
    ),
    (
        "8_docs",
        "PHASE 8 — Documentation. Write README.md with: (1) one-line "
        "project summary, (2) installation (pip install -e . or similar), "
        "(3) usage examples with expected output, (4) API reference listing "
        "main public functions/classes with their signatures. Also add "
        "docstrings to any undocumented public functions in {pkg}/.",
        600,
        lambda cwd, pkg: (cwd / "README.md").exists()
                        and (cwd / "README.md").stat().st_size > 200,
    ),
    (
        "9_optimize",
        "PHASE 9 — Optimization. Look at the code in {pkg}/ and identify "
        "the function most likely to be a performance hotspot (inner loops, "
        "repeated work, quadratic algorithms). Optimize it. Write a brief "
        "OPTIMIZATION.md explaining: (a) which function you targeted, "
        "(b) what was inefficient, (c) what you changed, (d) expected "
        "impact. All tests must still pass.",
        600,
        lambda cwd, pkg: (cwd / "OPTIMIZATION.md").exists(),
    ),
]


class SessionWatcher:
    def __init__(self, cwd: Path, since: float):
        self.cwd = cwd.resolve()
        self.since = since
        self.session_dir: Path | None = None
        self.messages: list[dict] = []

    def find_session(self) -> Path | None:
        if self.session_dir is not None:
            return self.session_dir
        if not SESSION_ROOT.exists():
            return None
        candidates = []
        for entry in SESSION_ROOT.iterdir():
            try:
                if not entry.is_dir():
                    continue
                if entry.stat().st_ctime < self.since - 2:
                    continue
                candidates.append((entry.stat().st_ctime, entry))
            except Exception:
                continue
        if not candidates:
            return None
        candidates.sort(reverse=True)
        self.session_dir = candidates[0][1]
        return self.session_dir

    def refresh(self) -> int:
        sd = self.find_session()
        if sd is None:
            return 0
        msgs = []
        for mf in sorted(sd.rglob("messages.jsonl")):
            try:
                for line in mf.read_text().strip().split("\n"):
                    if line.strip():
                        msgs.append(json.loads(line))
            except Exception:
                continue
        self.messages = msgs
        return len(msgs)

    def count_users(self) -> int:
        return sum(1 for m in self.messages if m.get("role") == "user")

    def last_is_final_text(self) -> bool:
        if not self.messages:
            return False
        last = self.messages[-1]
        if last.get("role") != "assistant":
            return False
        tc = last.get("tool_calls") or []
        content = (last.get("content") or "").strip()
        return not tc and len(content) > 0


def type_prompt(child, text: str) -> None:
    for ch in text:
        child.send(ch)
        time.sleep(0.008)
    time.sleep(0.3)
    child.send("\r")


def drain_pty(child, seconds: float = 1.0) -> None:
    cycles = max(int(seconds / 0.1), 1)
    for _ in range(cycles):
        try:
            child.expect(pexpect.TIMEOUT, timeout=0.1)
        except pexpect.EOF:
            break


def wait_for_prompt_landing(child, watcher, initial_ucount, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        drain_pty(child, 1.0)
        watcher.refresh()
        if watcher.count_users() > initial_ucount:
            return True
        time.sleep(0.5)
    return False


def wait_for_completion(child, watcher, max_wait=900, idle_threshold=180):
    start = time.time()
    last_count = len(watcher.messages)
    last_change = time.time()
    last_report = 0
    while time.time() - start < max_wait:
        drain_pty(child, 1.0)
        if not child.isalive():
            return "dead"
        watcher.refresh()
        now_count = len(watcher.messages)
        if now_count != last_count:
            last_count = now_count
            last_change = time.time()
        elapsed = int(time.time() - start)
        if elapsed - last_report >= 30:
            print(f"    [{elapsed}s] msgs={now_count}")
            last_report = elapsed
        if watcher.last_is_final_text():
            if time.time() - last_change >= 5:
                return "done"
        if time.time() - last_change >= idle_threshold:
            return "idle"
        time.sleep(1)
    return "timeout"


def run_functional_tests(cwd: Path) -> tuple[int, int]:
    ft = cwd / "functional_tests.sh"
    if not ft.exists():
        return 0, 0
    try:
        r = subprocess.run(["bash", str(ft)], cwd=str(cwd),
                         capture_output=True, text=True, timeout=120)
        out = r.stdout + r.stderr
        m = re.search(r"RESULT:\s*(\d+)\s+passed,\s*(\d+)\s+failed", out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return 0, 0


def run_comprehensive(cwd: Path, pkg: str, skip_build: bool = False) -> dict:
    """Run the 9-phase comprehensive loop. Returns results dict."""
    print(f"\n{'='*60}")
    print(f"  COMPREHENSIVE LOOP: {pkg}")
    print(f"  cwd: {cwd}")
    print(f"{'='*60}\n")

    # Restore PRD.md from master if available
    master = cwd / "PRD.master.md"
    if master.exists():
        shutil.copy2(master, cwd / "PRD.md")

    # Spawn TUI
    start_time = time.time()
    env = {**os.environ, "TERM": "xterm-256color", "COLUMNS": "120", "LINES": "30"}
    log_path = Path(f"/tmp/comp_{pkg}_{int(start_time)}.tui.log")
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

    watcher = SessionWatcher(cwd, since=start_time)
    results = []

    try:
        for phase_idx, (phase_name, prompt_tmpl, max_wait, validator) in enumerate(PHASES):
            if skip_build and phase_name == "2_build":
                print(f"\n─── Skipping phase {phase_name} (skip_build=True) ───")
                continue

            prompt = prompt_tmpl.format(pkg=pkg)
            print(f"\n─── Phase {phase_idx + 1}/9: {phase_name} ───")
            print(f"  prompt: {prompt[:100]}...")
            ucount = watcher.count_users()

            type_prompt(child, prompt)

            # On first phase, wait longer for session init
            if phase_idx == 0:
                print(f"  Waiting for session...", end="", flush=True)
                for i in range(300):
                    drain_pty(child, 1.0)
                    if watcher.find_session():
                        break
                    if i > 0 and i % 60 == 0:
                        print(f" ({i}s retype)", end="", flush=True)
                        type_prompt(child, prompt)
                if watcher.find_session():
                    print(f" found: {watcher.session_dir.name}")
                else:
                    print(f" NOT FOUND after 300s — aborting")
                    break

            if not wait_for_prompt_landing(child, watcher, ucount, timeout=120):
                print(f"  ⚠ prompt didn't land — retrying once")
                time.sleep(30)
                type_prompt(child, prompt)
                if not wait_for_prompt_landing(child, watcher, ucount, timeout=120):
                    print(f"  ✕ prompt never landed — skipping phase")
                    results.append({"phase": phase_name, "status": "prompt_lost", "validation": False})
                    continue

            print(f"  prompt landed, waiting (max {max_wait}s, 3min idle)...")
            status = wait_for_completion(child, watcher, max_wait=max_wait, idle_threshold=180)
            elapsed = int(time.time() - start_time)

            # Validate
            try:
                valid = validator(cwd, pkg)
            except Exception as e:
                valid = False
                print(f"  validator error: {e}")

            p, f = run_functional_tests(cwd)
            print(f"  phase done: status={status}, validation={valid}, "
                  f"func_tests={p}/{p+f} (at {elapsed}s total)")
            results.append({
                "phase": phase_name,
                "status": status,
                "validation": valid,
                "func_pass": p,
                "func_fail": f,
                "elapsed_total": elapsed,
            })

    finally:
        try:
            child.sendcontrol("c")
            time.sleep(1)
            child.terminate(force=True)
        except Exception:
            pass

    total = int(time.time() - start_time)
    phases_passed = sum(1 for r in results if r.get("validation"))
    final_p, final_f = run_functional_tests(cwd)

    print(f"\n{'='*60}")
    print(f"  COMPREHENSIVE RESULTS: {pkg}")
    print(f"  Total time: {total}s ({total/60:.1f}m)")
    print(f"  Phases completed: {len(results)}/9")
    print(f"  Phases validated: {phases_passed}/9")
    print(f"  Final functional tests: {final_p}/{final_p+final_f}")
    print(f"{'='*60}")
    for r in results:
        v = "✓" if r.get("validation") else "✗"
        print(f"  {r['phase']}: {v} ({r.get('status')}, ft={r.get('func_pass', '?')}/"
              f"{r.get('func_pass', 0) + r.get('func_fail', 0)})")

    report = {
        "pkg": pkg,
        "total_seconds": total,
        "phases": results,
        "phases_validated": phases_passed,
        "final_func_pass": final_p,
        "final_func_fail": final_f,
    }
    out = Path(f"/tmp/comp_{pkg}_results.json")
    with open(out, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n  Report: {out}")
    print(f"  TUI log: {log_path}")
    if watcher.session_dir:
        print(f"  Session: {watcher.session_dir}/messages.jsonl")

    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--pkg", required=True)
    parser.add_argument("--skip-build", action="store_true",
                       help="Skip phase 2 — assume code exists")
    args = parser.parse_args()
    report = run_comprehensive(Path(args.cwd).resolve(), args.pkg,
                                skip_build=args.skip_build)
    return 0 if report["phases_validated"] >= 7 else 1


if __name__ == "__main__":
    sys.exit(main())

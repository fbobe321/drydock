#!/usr/bin/env python3
"""RALPH-style test loop: build, run functional tests, send failures back
to the TUI to fix, repeat.

This is a more robust alternative to the fixed 24-step scripts. Instead
of trying to pipeline many prompts (which the TUI can't handle), it uses
a natural build-test-fix cycle:

  1. Tell TUI to build the package per PRD.md
  2. Wait until TUI says it's done (assistant text response, no tool calls)
  3. Run functional_tests.sh externally (we observe, TUI already worked)
  4. If failures, send the failure output back to the TUI: "fix these"
  5. Repeat until 0 failures or max iterations

Usage:
    python3 scripts/ralph_loop.py \\
        --cwd /data3/drydock_test_projects/408_lang_interp \\
        --pkg lang_interp \\
        --max-iterations 5
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


class SessionWatcher:
    """Poll the TUI's session log."""

    def __init__(self, cwd: Path, since: float):
        self.cwd = cwd.resolve()
        self.since = since
        self.session_dir: Path | None = None
        self.messages: list[dict] = []

    def find_session(self) -> Path | None:
        """Find the drydock session directory for our cwd.

        Problem: drydock only writes meta.json at session EXIT (not start).
        So we can't match on working_directory during the session.

        Strategy: Pick the newest session dir created after our start_time.
        If messages.jsonl exists in it (= drydock is actively writing), use it.
        """
        if self.session_dir is not None:
            return self.session_dir
        if not SESSION_ROOT.exists():
            return None

        # Find the newest dir that was created (birth time) at-or-after since
        candidates = []
        for entry in SESSION_ROOT.iterdir():
            try:
                if not entry.is_dir():
                    continue
                # Use directory creation time (birth) via stat
                st = entry.stat()
                # Directory's ctime usually = creation. Use mtime as fallback.
                dir_ctime = st.st_ctime
                if dir_ctime < self.since - 2:
                    continue
                candidates.append((dir_ctime, entry))
            except Exception:
                continue

        if not candidates:
            return None

        # Pick the newest. Take it even without messages.jsonl — drydock
        # may not flush messages until exit. Since only one drydock process
        # should be running, the newest dir created after our start is ours.
        candidates.sort(reverse=True)
        newest_ctime, newest_entry = candidates[0]
        self.session_dir = newest_entry
        return newest_entry

    def refresh(self) -> int:
        sd = self.find_session()
        if sd is None:
            return 0
        msgs: list[dict] = []
        for mf in sorted(sd.rglob("messages.jsonl")):
            try:
                for line in mf.read_text().strip().split("\n"):
                    if line.strip():
                        msgs.append(json.loads(line))
            except Exception:
                continue
        self.messages = msgs
        return len(msgs)

    def count_user_messages(self) -> int:
        return sum(1 for m in self.messages if m.get("role") == "user")

    def last_assistant_is_final(self) -> bool:
        """True if last assistant message has text content and no tool calls."""
        if not self.messages:
            return False
        last = self.messages[-1]
        if last.get("role") != "assistant":
            return False
        tc = last.get("tool_calls") or []
        content = (last.get("content") or "").strip()
        return not tc and len(content) > 0


def drain_pty(child: pexpect.spawn, seconds: float = 1.0) -> None:
    cycles = int(seconds / 0.1)
    for _ in range(max(cycles, 1)):
        try:
            child.expect(pexpect.TIMEOUT, timeout=0.1)
        except pexpect.EOF:
            break


def type_prompt(child: pexpect.spawn, text: str) -> None:
    """Type text into the TUI and press Enter."""
    for ch in text:
        child.send(ch)
        time.sleep(0.008)
    time.sleep(0.3)
    child.send("\r")


def wait_for_prompt_landing(child: pexpect.spawn, watcher: SessionWatcher,
                            initial_user_count: int, timeout: float = 60.0) -> bool:
    """Wait until a NEW user message appears in the session log."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        drain_pty(child, 1.0)
        watcher.refresh()
        if watcher.count_user_messages() > initial_user_count:
            return True
        time.sleep(0.5)
    return False


def wait_for_completion(child: pexpect.spawn, watcher: SessionWatcher,
                         max_wait: float = 1200.0,
                         idle_threshold: float = 180.0) -> str:
    """Wait for the TUI to finish responding to the most recent prompt.
    Completion = last message is assistant text with no tool calls,
                  OR idle_threshold seconds of no new messages.

    Returns:
      'done' if assistant gave final text response
      'idle' if went idle without text (possibly stuck)
      'timeout' if max_wait exceeded
      'dead' if TUI process died
    """
    start = time.time()
    last_msg_count = len(watcher.messages)
    last_change = time.time()
    last_report = 0

    while time.time() - start < max_wait:
        drain_pty(child, 1.0)
        if not child.isalive():
            return "dead"

        watcher.refresh()
        now_count = len(watcher.messages)
        if now_count != last_msg_count:
            last_msg_count = now_count
            last_change = time.time()

        # Progress report every 30s
        elapsed = int(time.time() - start)
        if elapsed - last_report >= 30:
            writes = sum(
                1 for m in watcher.messages
                if m.get("role") == "assistant"
                for tc in (m.get("tool_calls") or [])
                if tc.get("function", {}).get("name") in ("write_file", "search_replace")
            )
            print(f"  [{elapsed:4d}s] msgs={now_count} writes={writes}")
            last_report = elapsed

        # Done check
        if watcher.last_assistant_is_final():
            idle = time.time() - last_change
            if idle >= 3:  # small grace
                return "done"

        # Idle check (no new messages for a long time — stuck)
        if time.time() - last_change >= idle_threshold:
            return "idle"

        time.sleep(1.0)

    return "timeout"


def run_functional_tests(cwd: Path) -> tuple[int, int, str]:
    """Run bash functional_tests.sh. Returns (passed, failed, output)."""
    ft = cwd / "functional_tests.sh"
    if not ft.exists():
        return 0, 0, "No functional_tests.sh"
    try:
        r = subprocess.run(
            ["bash", str(ft)],
            cwd=str(cwd), capture_output=True, text=True, timeout=120,
        )
        out = r.stdout + r.stderr
        m = re.search(r"RESULT:\s*(\d+)\s*passed,\s*(\d+)\s*failed", out)
        if m:
            return int(m.group(1)), int(m.group(2)), out
        return 0, 999, out
    except subprocess.TimeoutExpired:
        return 0, 999, "TIMEOUT"


def clean_project(cwd: Path, pkg: str) -> None:
    """Reset project dir but preserve PRD.md and functional_tests.sh."""
    master = cwd / "PRD.master.md"
    target = cwd / "PRD.md"
    if master.exists():
        shutil.copy2(master, target)
    # Remove the package dir and any test/storage dirs
    pkg_dir = cwd / pkg
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    # Common storage/test dirs from previous runs
    for name in [f".{pkg}", "test_site", "test_project", "test_app",
                 ".forge", "test_app", ".doc_qa", ".stock_screener",
                 ".eval_harness", ".mini_db", ".prompt_optimizer",
                 "build", "dist"]:
        d = cwd / name
        if d.exists() and d.is_dir():
            shutil.rmtree(d)


def snapshot_package(cwd: Path, pkg: str) -> Path:
    """Copy the package dir to a snapshot. Returns snapshot path."""
    pkg_dir = cwd / pkg
    snap = Path(f"/tmp/ralph_snap_{pkg}_{int(time.time())}")
    if snap.exists():
        shutil.rmtree(snap)
    if pkg_dir.exists():
        shutil.copytree(pkg_dir, snap)
    else:
        snap.mkdir(parents=True, exist_ok=True)
    return snap


def restore_snapshot(cwd: Path, pkg: str, snap: Path) -> None:
    """Restore package dir from a snapshot."""
    pkg_dir = cwd / pkg
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    if snap.exists() and any(snap.iterdir()):
        shutil.copytree(snap, pkg_dir)


def ralph(cwd: Path, pkg: str, max_iterations: int = 5,
          fresh_build: bool = False) -> int:
    """Main RALPH loop.

    If fresh_build=True, wipe existing package and start from scratch.
    If fresh_build=False (default), keep existing partial build and
    iterate on it. Safer — prevents data loss on harness failures.
    """
    print(f"\n{'='*60}")
    print(f"  RALPH LOOP: {pkg}")
    print(f"  cwd: {cwd}")
    print(f"  max iterations: {max_iterations}")
    print(f"  fresh build: {fresh_build}")
    print(f"{'='*60}\n")

    if fresh_build:
        clean_project(cwd, pkg)
    else:
        # Just restore PRD.md from master
        master = cwd / "PRD.master.md"
        target = cwd / "PRD.md"
        if master.exists():
            shutil.copy2(master, target)

    # Baseline: current state of tests
    p, f, _ = run_functional_tests(cwd)
    print(f"  Baseline ({'no build' if fresh_build else 'existing build'}): "
          f"{p}/{p+f} tests pass")
    baseline_pass = p
    baseline_fail = f

    log_path = Path(f"/tmp/ralph_{pkg}_{int(time.time())}.tui.log")
    start_time = time.time()
    env = {**os.environ, "TERM": "xterm-256color", "COLUMNS": "120", "LINES": "30"}
    child = pexpect.spawn(DRYDOCK_BIN, encoding="utf-8", timeout=5,
                          maxread=100000, env=env, cwd=str(cwd))
    child.logfile_read = open(log_path, "w")

    try:
        child.expect([r">", r"Drydock", r"┌"], timeout=30)
    except Exception:
        pass
    time.sleep(2)

    # Dismiss trust dialog if present
    try:
        if "Trust this folder" in (child.before or ""):
            child.send("\x1b[D")
            time.sleep(0.2)
            child.send("\r")
            time.sleep(2)
    except Exception:
        pass

    watcher = SessionWatcher(cwd, since=start_time)
    iteration_results = []

    try:
        # ── Iteration 0: Initial build ──
        if baseline_fail == 0 and baseline_pass > 0:
            initial_prompt = (
                f"Look at the {pkg}/ directory and functional_tests.sh. "
                f"All tests already pass. Verify by running: bash functional_tests.sh"
            )
        elif baseline_pass > 0:
            # Partial build — just fix failures
            initial_prompt = (
                f"Look at the existing {pkg}/ directory. Run the tests: "
                f"bash functional_tests.sh. Some tests fail — fix them. "
                f"Read the failing source files and apply targeted fixes."
            )
        else:
            # No build or nothing works — full rebuild
            initial_prompt = (
                f"Read PRD.md and build the {pkg} package as specified. "
                f"Create ALL files listed in the PRD under {pkg}/. "
                f"Do not skip any files. Do not stop until every file is written. "
                f"Write __init__.py, __main__.py, and cli.py FIRST before other modules "
                f"(this ensures the package is importable before other code depends on it)."
            )

        print(f"\n─── Iteration 0: Initial build ───")
        print(f"  Typing initial prompt ({len(initial_prompt)} chars)...")
        initial_ucount = 0  # fresh TUI, no user messages yet

        # Type the prompt — drydock creates a session when it receives input
        type_prompt(child, initial_prompt)

        # Now wait for the session to appear — 5 min. Drydock can take 3-4
        # minutes to create the session dir when GPU is busy from a prior run.
        print(f"  Waiting for session to appear...", end="", flush=True)
        session_found = False
        for i in range(300):  # up to 300s (5 min)
            drain_pty(child, 1.0)
            if watcher.find_session():
                session_found = True
                break
            if i > 0 and i % 60 == 0:
                print(f" (still waiting at {i}s, retyping)", end="", flush=True)
                time.sleep(2)
                type_prompt(child, initial_prompt)
        if session_found:
            print(f" found: {watcher.session_dir.name}")
        else:
            print(f" NOT FOUND after 300s")
            return 1

        # Wait for user message to actually appear in session log
        if not wait_for_prompt_landing(child, watcher, initial_ucount, timeout=90):
            print(f"  [retrying initial prompt]")
            time.sleep(5)
            type_prompt(child, initial_prompt)
            if not wait_for_prompt_landing(child, watcher, initial_ucount, timeout=60):
                print(f"  ERROR: initial prompt did not land in session log")
                return 1
        print(f"  prompt landed")

        print(f"  waiting for build to finish (up to 20 min, idle=3min)...")
        status = wait_for_completion(child, watcher, max_wait=1200, idle_threshold=180)
        elapsed = int(time.time() - start_time)
        print(f"  iter 0 status: {status} ({elapsed}s)")

        p, f, out = run_functional_tests(cwd)
        iteration_results.append({"iter": 0, "pass": p, "fail": f, "status": status})
        print(f"  iter 0 tests: {p}/{p+f} pass")

        # ── Iteration 1+: Fix failures ──
        best_pass = p
        best_fail = f
        best_snap = snapshot_package(cwd, pkg)
        print(f"  [snapshot saved: {best_pass}/{best_pass+best_fail}]")

        for it in range(1, max_iterations + 1):
            if f == 0:
                print(f"\n  ✓ All tests pass! Stopping at iteration {it-1}.")
                break

            print(f"\n─── Iteration {it}: Fix failures ───")
            # Extract just the FAIL lines and last bit of context
            fail_lines = [ln for ln in out.split("\n") if ln.startswith("FAIL:")]
            fail_text = "\n".join(fail_lines[:10])
            # Also include the current passing tests so model doesn't regress them
            pass_lines = [ln for ln in out.split("\n") if ln.startswith("PASS:")]
            pass_text = "\n".join(pass_lines)
            fix_prompt = (
                f"The functional tests have {p} passing and {f} failing.\n\n"
                f"PASSING tests (DO NOT BREAK THESE):\n{pass_text}\n\n"
                f"FAILING tests to fix:\n{fail_text}\n\n"
                f"For EACH failure, use search_replace to make a TARGETED fix. "
                f"Do not rewrite entire files. Do not modify code that the passing "
                f"tests depend on. Make the MINIMUM change needed to fix each failure. "
                f"Then confirm you've fixed them. Do NOT run the tests yourself — just fix the code."
            )
            ucount = watcher.count_user_messages()
            type_prompt(child, fix_prompt)
            if not wait_for_prompt_landing(child, watcher, ucount, timeout=120):
                # Retry with longer pause first
                print(f"  [fix prompt didn't land in 120s — waiting 30s + retrying]")
                time.sleep(30)
                drain_pty(child, 5.0)
                type_prompt(child, fix_prompt)
                if not wait_for_prompt_landing(child, watcher, ucount, timeout=120):
                    print(f"  ERROR: iter {it} fix prompt did not land after retry")
                    break
            print(f"  fix prompt landed, waiting for response...")
            status = wait_for_completion(child, watcher, max_wait=900, idle_threshold=180)
            elapsed = int(time.time() - start_time)
            print(f"  iter {it} status: {status} ({elapsed}s)")

            p, f, out = run_functional_tests(cwd)
            print(f"  iter {it} tests: {p}/{p+f} pass (was {best_pass}/{best_pass+best_fail})")

            # ROLLBACK if regressed
            if p < best_pass:
                print(f"  ⚠ REGRESSION — rolling back to {best_pass}/{best_pass+best_fail}")
                restore_snapshot(cwd, pkg, best_snap)
                # Re-verify the rollback
                p, f, out = run_functional_tests(cwd)
                print(f"  after rollback: {p}/{p+f} pass")
                iteration_results.append({
                    "iter": it, "pass": p, "fail": f,
                    "status": status, "rolled_back": True,
                })
            else:
                iteration_results.append({
                    "iter": it, "pass": p, "fail": f, "status": status,
                })
                # Update best if improved or equal
                if p > best_pass:
                    best_pass = p
                    best_fail = f
                    # Clean up old snap
                    if best_snap.exists():
                        shutil.rmtree(best_snap)
                    best_snap = snapshot_package(cwd, pkg)
                    print(f"  [new best snapshot: {best_pass}/{best_pass+best_fail}]")

    finally:
        try:
            child.sendcontrol("c")
            time.sleep(0.5)
            child.terminate(force=True)
        except Exception:
            pass

    # ── Report ──
    total = int(time.time() - start_time)
    print(f"\n{'='*60}")
    print(f"  RALPH RESULTS: {pkg}")
    print(f"  Total time: {total}s ({total/60:.1f}m)")
    print(f"  Iterations: {len(iteration_results)}")
    print(f"{'='*60}")
    for r in iteration_results:
        print(f"  iter {r['iter']}: {r['pass']}/{r['pass']+r['fail']} pass "
              f"(status={r['status']})")
    final = iteration_results[-1] if iteration_results else {"pass": 0, "fail": 999}
    print(f"\n  FINAL: {final['pass']}/{final['pass']+final['fail']} tests pass")
    print(f"  TUI log: {log_path}")
    if watcher.session_dir:
        print(f"  Session: {watcher.session_dir}/messages.jsonl")

    # Save results
    results_path = Path(f"/tmp/ralph_{pkg}_results.json")
    with open(results_path, "w") as rf:
        json.dump({
            "pkg": pkg,
            "total_seconds": total,
            "iterations": iteration_results,
        }, rf, indent=2)
    print(f"  Results: {results_path}")

    return 0 if final["fail"] == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--pkg", required=True)
    parser.add_argument("--max-iterations", type=int, default=5)
    args = parser.parse_args()
    return ralph(Path(args.cwd).resolve(), args.pkg, args.max_iterations)


if __name__ == "__main__":
    sys.exit(main())

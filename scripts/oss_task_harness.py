#!/usr/bin/env python3
"""OSS Task Harness — drive drydock on real open-source projects.

Unlike green-field PRD testing (ralph_loop.py), this drives drydock to work
WITHIN an existing codebase. Tests drydock's ability to:
  - Understand unfamiliar code
  - Add features without breaking existing tests (regression check)
  - Fix documented bugs
  - Not make excessive unrelated changes (diff minimality)

Workflow per task:
  1. Clone repo at specific commit (or reuse clean worktree)
  2. Run existing tests (baseline must pass — if not, task is ill-defined)
  3. Give drydock a TASK.md (what to do + acceptance criteria)
  4. Wait for drydock to complete
  5. Re-run existing tests (REGRESSION CHECK)
  6. Run new tests if task added any (FEATURE CHECK)
  7. Check git diff size (MINIMALITY CHECK)
  8. Report pass/fail per criterion

Usage:
  python3 scripts/oss_task_harness.py \\
      --workdir /data3/drydock_oss_tests/shortuuid \\
      --repo https://github.com/skorokithakis/shortuuid \\
      --commit main \\
      --task /path/to/TASK.md
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


def sh(cmd: str, cwd: str | None = None, timeout: int = 300) -> tuple[int, str, str]:
    """Run shell command. Returns (rc, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd,
                          capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"


def setup_repo(workdir: Path, repo_url: str, commit: str) -> bool:
    """Clone repo fresh or reset existing to specified commit."""
    workdir.parent.mkdir(parents=True, exist_ok=True)
    if (workdir / ".git").exists():
        # Reset existing
        rc, _, err = sh("git reset --hard HEAD && git clean -fd", cwd=str(workdir))
        if rc != 0:
            print(f"  reset failed: {err[:200]}")
        rc, _, _ = sh(f"git checkout {commit}", cwd=str(workdir))
        if rc != 0:
            print(f"  checkout failed; recloning")
            shutil.rmtree(workdir)
        else:
            return True
    # Fresh clone
    rc, out, err = sh(f"git clone {repo_url} {workdir}", timeout=600)
    if rc != 0:
        print(f"  clone failed: {err[:200]}")
        return False
    if commit and commit != "main" and commit != "master":
        rc, _, _ = sh(f"git checkout {commit}", cwd=str(workdir))
    return rc == 0


def run_tests(workdir: Path, test_cmd: str, timeout: int = 300) -> tuple[bool, int, int, str]:
    """Run the project's tests. Returns (all_pass, passed, failed, output_tail)."""
    rc, stdout, stderr = sh(test_cmd, cwd=str(workdir), timeout=timeout)
    out = stdout + stderr
    # Try to parse pytest-style output: "N passed, M failed"
    m = re.search(r"(\d+)\s+passed(?:,\s*(\d+)\s+failed)?", out)
    if m:
        passed = int(m.group(1))
        failed = int(m.group(2) or 0)
        return rc == 0 and failed == 0, passed, failed, out[-1500:]
    # Fallback: just use rc
    return rc == 0, 0, 0, out[-1500:]


class SessionWatcher:
    """Tracks drydock session messages."""

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


def drive_drydock(workdir: Path, task_text: str, max_seconds: int = 2400) -> str:
    """Spawn drydock, give it the task, wait for completion.
    Returns: 'done' | 'idle' | 'timeout' | 'dead'
    """
    log_path = Path(f"/tmp/oss_drydock_{int(time.time())}.log")
    start_time = time.time()
    env = {**os.environ, "TERM": "xterm-256color", "COLUMNS": "120", "LINES": "30"}
    child = pexpect.spawn(DRYDOCK_BIN, encoding="utf-8", timeout=5,
                          maxread=100000, env=env, cwd=str(workdir))
    child.logfile_read = open(log_path, "w")
    try:
        child.expect([r">", r"Drydock", r"┌"], timeout=30)
    except Exception:
        pass
    time.sleep(3)

    # Dismiss trust dialog if present
    try:
        if "Trust this folder" in (child.before or ""):
            child.send("\x1b[D")
            time.sleep(0.2)
            child.send("\r")
            time.sleep(2)
    except Exception:
        pass

    watcher = SessionWatcher(workdir, since=start_time)

    # Type task
    type_prompt(child, task_text)

    # Wait for session to appear (up to 5 min)
    print(f"  Waiting for session...", end="", flush=True)
    for i in range(300):
        drain_pty(child, 1.0)
        if watcher.find_session():
            print(f" found: {watcher.session_dir.name}")
            break
        if i > 0 and i % 60 == 0:
            print(f" ({i}s, retyping)", end="", flush=True)
            type_prompt(child, task_text)
    if not watcher.find_session():
        print(f" NOT FOUND")
        try:
            child.terminate(force=True)
        except Exception:
            pass
        return "dead"

    # Wait for initial user message to land
    deadline = time.time() + 120
    while time.time() < deadline:
        drain_pty(child, 1.0)
        watcher.refresh()
        if watcher.count_users() > 0:
            break

    # Wait for completion
    last_count = len(watcher.messages)
    last_change = time.time()
    last_report = 0
    result = "timeout"
    while time.time() - start_time < max_seconds:
        drain_pty(child, 1.0)
        if not child.isalive():
            result = "dead"
            break
        watcher.refresh()
        now_count = len(watcher.messages)
        if now_count != last_count:
            last_count = now_count
            last_change = time.time()
        elapsed = int(time.time() - start_time)
        if elapsed - last_report >= 30:
            print(f"  [{elapsed}s] msgs={now_count}")
            last_report = elapsed
        if watcher.last_is_final_text():
            if time.time() - last_change >= 5:
                result = "done"
                break
        if time.time() - last_change >= 300:  # 5 min idle
            result = "idle"
            break
        time.sleep(1)

    try:
        child.sendcontrol("c")
        time.sleep(1)
        child.terminate(force=True)
    except Exception:
        pass
    return result


def run_task(workdir: Path, repo_url: str, commit: str,
             task_file: Path, test_cmd: str,
             baseline_test_cmd: str | None = None,
             validate_cmd: str | None = None) -> dict:
    """Run a complete OSS task cycle. Returns report dict."""
    report = {
        "workdir": str(workdir),
        "repo": repo_url,
        "commit": commit,
        "task": str(task_file),
    }
    print(f"\n{'='*60}")
    print(f"  OSS TASK: {task_file.name}")
    print(f"  repo: {repo_url}")
    print(f"  commit: {commit}")
    print(f"  workdir: {workdir}")
    print(f"{'='*60}\n")

    # ── 1. Setup ──
    print("1. Setting up repo...")
    if not setup_repo(workdir, repo_url, commit):
        report["error"] = "setup failed"
        return report

    # Copy task into workdir as TASK.md so drydock can read it
    shutil.copy2(task_file, workdir / "TASK.md")
    print(f"   TASK.md copied to {workdir}")

    # ── 2. Baseline tests ──
    print(f"2. Running baseline tests: {baseline_test_cmd or test_cmd}")
    baseline_pass, bp, bf, btail = run_tests(workdir, baseline_test_cmd or test_cmd)
    print(f"   baseline: {bp} passed, {bf} failed, all_pass={baseline_pass}")
    report["baseline_pass"] = baseline_pass
    report["baseline_passed"] = bp
    report["baseline_failed"] = bf
    if not baseline_pass:
        print("   WARNING: baseline tests don't pass. Task may be ill-defined.")

    # ── 3. Drive drydock ──
    print("3. Driving drydock...")
    task_text = (
        f"Read TASK.md and complete the task described there. "
        f"Make TARGETED changes. Do NOT break existing tests. "
        f"When done, respond with a brief summary."
    )
    drydock_status = drive_drydock(workdir, task_text, max_seconds=2400)
    report["drydock_status"] = drydock_status
    print(f"   drydock status: {drydock_status}")

    # ── 4. Regression check ──
    print(f"4. Regression check: {test_cmd}")
    after_pass, ap, af, atail = run_tests(workdir, test_cmd)
    report["after_pass"] = after_pass
    report["after_passed"] = ap
    report["after_failed"] = af
    report["after_output_tail"] = atail
    print(f"   after: {ap} passed, {af} failed, all_pass={after_pass}")

    # ── 5. Regression verdict ──
    if baseline_pass and not after_pass:
        report["regression"] = True
        print(f"   ⚠ REGRESSION: drydock broke existing tests")
    else:
        report["regression"] = False

    # ── 6. Diff size ──
    rc, diff_out, _ = sh("git diff --shortstat", cwd=str(workdir))
    report["diff_summary"] = diff_out.strip()
    print(f"   diff: {diff_out.strip()}")

    # ── 7. Feature validation (prevents non-functional scaffolding) ──
    if validate_cmd:
        print(f"5. Feature validation: {validate_cmd}")
        vrc, vout, verr = sh(validate_cmd, cwd=str(workdir), timeout=60)
        report["validation_rc"] = vrc
        report["validation_output"] = (vout + verr)[-1000:]
        report["validation_pass"] = (vrc == 0)
        print(f"   validation: {'PASS' if vrc == 0 else 'FAIL'} (rc={vrc})")
        if vrc != 0:
            print(f"   output: {(vout + verr)[-500:]}")
    else:
        report["validation_pass"] = None  # not tested

    overall_pass = (after_pass and not report['regression']
                    and (report.get('validation_pass') is not False))
    print(f"\n{'─'*60}")
    print(f"  RESULT: {'PASS' if overall_pass else 'FAIL'}")
    print(f"{'─'*60}\n")

    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--commit", default="main")
    parser.add_argument("--task", required=True)
    parser.add_argument("--test-cmd", required=True)
    parser.add_argument("--baseline-test-cmd", default=None,
                       help="Defaults to --test-cmd")
    parser.add_argument("--validate-cmd", default=None,
                       help="Optional validation command run after task. If "
                            "non-zero exit, marks overall as FAIL. Use for "
                            "runtime feature checks (prevents scaffolding bugs).")
    args = parser.parse_args()

    report = run_task(
        Path(args.workdir).resolve(),
        args.repo,
        args.commit,
        Path(args.task).resolve(),
        args.test_cmd,
        args.baseline_test_cmd,
        args.validate_cmd,
    )

    out_path = Path(f"/tmp/oss_task_{Path(args.task).stem}_{int(time.time())}.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nFull report: {out_path}")
    return 0 if (report.get("after_pass") and not report.get("regression")) else 1


if __name__ == "__main__":
    sys.exit(main())

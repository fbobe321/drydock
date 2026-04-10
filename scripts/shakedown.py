#!/usr/bin/env python3
"""Shakedown harness for the drydock TUI.

Reproduces the failure modes a real user experiences but the existing
tui_test.py harness misses:

- Write loops (model rewrites the same file with identical content N times)
- Ignored user interrupts (user types "stop", model continues looping)
- Search/replace cascades (search_replace fails 2+ times in a row)
- Subjective hangs (no NEW useful tool result for 60+ seconds)
- Final-state failures (package doesn't actually run)

The harness behaves like a real user: types a vague prompt, watches the
session live, types follow-up "you are in a loop" messages when it detects
runaway behavior, and judges the run on user-perceptible criteria — not
on tool-call counts.

Pass criteria (ALL must hold):
  1. NO write loops (≥3 identical-content writes to the same path)
  2. NO ignored user interrupts (after the harness types "stop", the model
     must not produce another identical-content write to the same path)
  3. NO search_replace failure cascade (≥3 failures in a row)
  4. The final package must execute: `python3 -m <pkg> --help` exits 0
  5. Session must finish within MAX_SESSION_SECONDS

Exit code 0 = pass, non-zero = fail (with detailed report on stdout).

Usage:
    python3 scripts/shakedown.py --cwd /data3/test_drydock \\
        --prompt "review the PRD and get started" \\
        --pkg doc_qa_system
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
from collections import Counter, defaultdict
from pathlib import Path

import pexpect

DRYDOCK_BIN = "/home/bobef/miniforge3/envs/drydock/bin/drydock"
SESSION_ROOT = Path.home() / ".vibe" / "logs" / "session"

# Failure thresholds (user-perceptible pain)
DUP_WRITE_LOOP_THRESHOLD = 3       # 3+ identical-content writes = loop
SEARCH_REPLACE_FAIL_CASCADE = 3    # 3+ failed search_replace in a row
DEAD_SILENCE_SECONDS = 240         # No new MESSAGES at all for 4 min = stuck.
                                   # Local Gemma 4 with thinking="high" can
                                   # take 90-120s on a complex first turn —
                                   # 120s was too aggressive and killed real
                                   # progress mid-thinking.
MAX_SESSION_SECONDS = 720          # Hard cap on session time (12 minutes)
INTERRUPT_GRACE_TURNS = 2          # After we type "stop", give 2 turns
DONE_GRACE_SECONDS = 30            # After model declares done (text-only), give it


# ────────────────────────────────────────────────────────────────────────
# Session log polling
# ────────────────────────────────────────────────────────────────────────

class SessionWatcher:
    """Tails the live messages.jsonl for the current drydock session."""

    def __init__(self, cwd: Path, since: float):
        self.cwd = cwd.resolve()
        self.since = since  # epoch seconds — only sessions started after this
        self.session_dir: Path | None = None
        self.last_seen_msg = 0
        self.messages: list[dict] = []

    def find_session(self) -> Path | None:
        """Locate the new session that matches our cwd, started after `since`."""
        if self.session_dir is not None:
            return self.session_dir
        for entry in sorted(SESSION_ROOT.iterdir(), reverse=True):
            try:
                if not entry.is_dir():
                    continue
                if entry.stat().st_mtime < self.since - 5:
                    continue
                meta = json.loads((entry / "meta.json").read_text())
                wd = meta.get("environment", {}).get("working_directory", "")
                if str(self.cwd) == wd:
                    self.session_dir = entry
                    return entry
            except Exception:
                continue
        return None

    def refresh(self) -> list[dict]:
        """Re-read messages from the main session AND any sub-agent sessions.

        Drydock writes the BUILDER (and other) subagent message logs at
        `<main_session>/agents/<sub_session>/messages.jsonl`. The shakedown
        harness used to watch only the main session, so when the main agent
        delegated to the builder, it looked like the session was idle for
        the entire build (4 main messages, ~30 sub messages of real work).
        Now we walk every messages.jsonl under the main session and merge
        them into one ordered list (main first, then each sub-session by
        directory name which encodes start time).
        """
        sd = self.find_session()
        if sd is None:
            return []

        msg_files: list[Path] = []
        main_file = sd / "messages.jsonl"
        if main_file.exists():
            msg_files.append(main_file)
        # Sub-sessions
        agents_dir = sd / "agents"
        if agents_dir.exists():
            for sub in sorted(agents_dir.iterdir()):
                sub_msgs = sub / "messages.jsonl"
                if sub_msgs.exists():
                    msg_files.append(sub_msgs)

        if not msg_files:
            return []

        msgs: list[dict] = []
        for f in msg_files:
            try:
                lines = f.read_text().strip().split("\n")
            except Exception:
                continue
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    msgs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        new_msgs = msgs[self.last_seen_msg:]
        self.last_seen_msg = len(msgs)
        self.messages = msgs
        return new_msgs


# ────────────────────────────────────────────────────────────────────────
# Failure detectors — derived from session messages
# ────────────────────────────────────────────────────────────────────────

class FailureDetector:
    """Walks messages and tracks user-pain failure modes."""

    def __init__(self):
        # Per-path: list of (msg_index, content, bytes_written) for write_file
        # calls. bytes_written is read from the matching tool result and
        # lets us distinguish a "real" write from a dedup'd no-op (=0) or
        # a hard-blocked write (=None / error).
        self.writes: dict[str, list[tuple[int, str, int | None]]] = defaultdict(list)
        # search_replace results in order
        self.search_replace_results: list[tuple[int, bool]] = []  # (msg_idx, success)
        # Last index that produced a "useful" tool result
        self.last_useful_idx = 0
        self.last_useful_time = time.time()

        # Pain markers
        self.write_loops_detected: list[tuple[str, int]] = []  # (path, count)
        self.search_replace_cascades: list[int] = []           # starting msg idx
        self.user_interrupts_sent: list[int] = []               # msg idx at time of interrupt
        self.interrupted_paths: set[str] = set()
        self.ignored_after_interrupt: list[str] = []           # paths still being looped after stop
        self.silent_intervals: list[float] = []                # seconds of pure silence

    def feed(self, all_msgs: list[dict]) -> None:
        """Re-process the FULL message list (cheap; runs each poll)."""
        # Reset derived state
        self.writes.clear()
        self.search_replace_results.clear()

        # Pass 1: collect write_file calls with their args
        # We need the matching tool result to know bytes_written, so we
        # walk indexed and look ahead for the next tool message.
        for i, m in enumerate(all_msgs):
            role = m.get("role", "")
            if role == "assistant":
                for tc in (m.get("tool_calls") or []):
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    args_raw = fn.get("arguments", "")
                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(args, dict):
                        continue
                    if name == "write_file":
                        path = args.get("path", "")
                        content = args.get("content", "")
                        if not path:
                            continue
                        # Look ahead for the matching tool result
                        bytes_written: int | None = None
                        for j in range(i + 1, min(i + 4, len(all_msgs))):
                            tm = all_msgs[j]
                            if tm.get("role") != "tool":
                                continue
                            tc_id = tc.get("id", "")
                            tm_id = tm.get("tool_call_id", "")
                            if tc_id and tm_id and tc_id != tm_id:
                                continue
                            tcontent = str(tm.get("content", "") or "")
                            if tcontent.startswith("<tool_error>"):
                                bytes_written = None  # blocked
                            else:
                                bw_match = re.search(
                                    r"bytes_written:\s*(\d+)", tcontent
                                )
                                if bw_match:
                                    bytes_written = int(bw_match.group(1))
                            break
                        self.writes[path].append((i, content, bytes_written))
            elif role == "tool":
                content = str(m.get("content", "") or "")
                tool_name = m.get("name", "")
                if tool_name == "search_replace":
                    is_err = content.startswith("<tool_error>") or "failed:" in content[:80].lower()
                    self.search_replace_results.append((i, not is_err))

        # Now derive failures.
        #
        # A "real" write loop is 3+ consecutive identical-content writes
        # where ≥2 of them ACTUALLY wrote bytes (bytes_written > 0). The
        # hard block + dedup combo lets a model spam write_file with the
        # same content — only the FIRST one writes bytes, the rest are
        # dedup no-ops or blocked. That's not a loop the harness should
        # fail on; it's the framework working as intended.
        self.write_loops_detected = []
        for path, entries in self.writes.items():
            if not entries:
                continue
            run_content = entries[0][1]
            run_writes: list[int | None] = [entries[0][2]]
            run_len = 1
            for _, content, bw in entries[1:]:
                if content == run_content:
                    run_len += 1
                    run_writes.append(bw)
                else:
                    if run_len >= DUP_WRITE_LOOP_THRESHOLD:
                        real_writes = sum(
                            1 for x in run_writes if x is not None and x > 0
                        )
                        if real_writes >= 2:
                            self.write_loops_detected.append((path, run_len))
                    run_content = content
                    run_writes = [bw]
                    run_len = 1
            if run_len >= DUP_WRITE_LOOP_THRESHOLD:
                real_writes = sum(
                    1 for x in run_writes if x is not None and x > 0
                )
                if real_writes >= 2:
                    self.write_loops_detected.append((path, run_len))

        # Search replace cascades
        self.search_replace_cascades = []
        run_start = None
        run_len = 0
        for idx, ok in self.search_replace_results:
            if not ok:
                if run_start is None:
                    run_start = idx
                run_len += 1
                if run_len >= SEARCH_REPLACE_FAIL_CASCADE:
                    self.search_replace_cascades.append(run_start)
            else:
                run_start = None
                run_len = 0

    def update_useful_clock(self, all_msgs: list[dict]) -> None:
        """Track wall-clock time of the most recent message (any role).

        We treat *any* new message as a sign of life: an assistant tool call,
        a tool result, even an assistant thinking-only message.
        """
        if len(all_msgs) > self.last_useful_idx:
            self.last_useful_idx = len(all_msgs)
            self.last_useful_time = time.time()

    def silent_seconds(self) -> float:
        return time.time() - self.last_useful_time

    def model_declared_done(self, all_msgs: list[dict]) -> bool:
        """Last assistant message is text-only with no tool call → model done.

        We strip thinking tokens before judging — a message that is *just*
        thinking does not count as "done", it counts as "still generating".
        """
        for m in reversed(all_msgs):
            if m.get("role") == "assistant":
                if m.get("tool_calls"):
                    return False
                content = str(m.get("content", "") or "")
                stripped = re.sub(
                    r"<\|channel>.*?<channel\|>", "", content, flags=re.DOTALL
                ).strip()
                return bool(stripped)
            if m.get("role") == "tool":
                return False
        return False

    def loop_path_needs_interrupt(self) -> str | None:
        """Return a path that's looping and we haven't interrupted yet."""
        for path, count in self.write_loops_detected:
            if path not in self.interrupted_paths:
                return path
        return None

    def check_interrupt_obeyed(self, all_msgs: list[dict]) -> None:
        """For paths we interrupted, check if model wrote them again identically.

        Only counts as "ignored" if the post-interrupt writes ACTUALLY wrote
        bytes (bytes_written > 0). Dedup'd no-op writes don't count — those
        are already caught by the framework.
        """
        for path in list(self.interrupted_paths):
            entries = self.writes.get(path, [])
            interrupt_idx = max(self.user_interrupts_sent) if self.user_interrupts_sent else 0
            # Now (idx, content, bytes_written) tuples
            after = [
                (idx, c, bw) for idx, c, bw in entries if idx > interrupt_idx
            ]
            if len(after) >= 2:
                # Two writes to the same path AFTER the user said stop with
                # IDENTICAL content AND both actually wrote bytes = ignored.
                last, prev = after[-1], after[-2]
                if (last[1] == prev[1]
                        and (last[2] or 0) > 0
                        and (prev[2] or 0) > 0):
                    if path not in self.ignored_after_interrupt:
                        self.ignored_after_interrupt.append(path)


# ────────────────────────────────────────────────────────────────────────
# Drydock TUI driver
# ────────────────────────────────────────────────────────────────────────

class DrydockDriver:
    def __init__(self, cwd: Path, log_path: Path):
        self.cwd = cwd
        self.log_path = log_path
        self.child: pexpect.spawn | None = None
        self.log_file = None

    def spawn(self) -> None:
        self.log_file = open(self.log_path, "wb")
        env = {
            **os.environ,
            "TERM": "xterm-256color",
            "COLUMNS": "120",
            "LINES": "30",
        }
        self.child = pexpect.spawn(
            DRYDOCK_BIN,
            encoding="utf-8",
            timeout=5,
            maxread=100000,
            env=env,
            cwd=str(self.cwd),
        )
        self.child.logfile_read = open(self.log_path, "w")
        # Wait for the TUI to draw its prompt
        self.child.expect([r">", r"┌", r"Drydock"], timeout=30)
        time.sleep(2)  # let the UI fully render

        # If the "Trust this folder?" dialog is up, dismiss it by pressing
        # Left arrow (move from "No" to "Yes") then Enter. Drydock blocks
        # input on this dialog, so the harness used to time out forever.
        try:
            after = self.child.before or ""
        except Exception:
            after = ""
        if "Trust this folder" in after or "trust this folder" in after.lower():
            print("[*] Trust dialog detected — answering Yes")
            self.child.send("\x1b[D")  # Left arrow
            time.sleep(0.2)
            self.child.send("\r")      # Enter
            time.sleep(2)              # let the UI redraw the main view

    def type_message(self, text: str) -> None:
        """Type a message into the TUI's input box and submit it."""
        if self.child is None:
            raise RuntimeError("driver not spawned")
        # Type character by character — Textual needs real keypresses
        for ch in text:
            self.child.send(ch)
            time.sleep(0.01)
        time.sleep(0.2)
        self.child.send("\r")  # submit

    def dismiss_permission_prompts(self) -> bool:
        """Detect and auto-approve any tool permission dialog.

        Drydock pops a "Tool wants to do X. Allow?" prompt for tools whose
        permission is ASK. The default cursor is on "Yes" (option 1), so
        pressing Enter approves. We do nothing if no prompt is up.

        Returns True if a prompt was dismissed (signal for the caller to
        give the UI a moment to redraw).
        """
        if self.child is None:
            return False
        # Peek at recent buffer without consuming
        try:
            recent = self.child.before or ""
        except Exception:
            return False
        # Markers seen on the actual permission prompts: numbered "Yes" /
        # "Yes and always allow" / "No and tell the agent" choices.
        markers = (
            "Yes and always allow",
            "and tell the agent what to do",
            "Allow this tool to run",
        )
        if not any(m in recent for m in markers):
            return False
        # Default cursor is on "Yes" — Enter accepts
        self.child.send("\r")
        time.sleep(0.5)
        return True

    def alive(self) -> bool:
        return self.child is not None and self.child.isalive()

    def close(self) -> None:
        if self.child is not None and self.child.isalive():
            try:
                self.child.sendcontrol("c")
                time.sleep(0.5)
                self.child.terminate(force=True)
            except Exception:
                pass


# ────────────────────────────────────────────────────────────────────────
# Test runner
# ────────────────────────────────────────────────────────────────────────

def run_test(cwd: Path, prompt: str, pkg: str) -> int:
    print(f"┌────────────────────────────────────────────────────")
    print(f"│ shakedown")
    print(f"│   cwd:    {cwd}")
    print(f"│   prompt: {prompt}")
    print(f"│   pkg:    {pkg}")
    print(f"└────────────────────────────────────────────────────")

    # Reset cwd to a known clean state:
    #   1. Restore PRD.md from PRD.master.md if present (previous runs
    #      contaminate it).
    #   2. Delete the package dir.
    #   3. Delete any data directories that previous runs may have created.
    master = cwd / "PRD.master.md"
    target = cwd / "PRD.md"
    if master.exists():
        shutil.copy2(master, target)
        print(f"[*] Restored PRD.md from PRD.master.md ({target.stat().st_size}b)")

    pkg_dir = cwd / pkg
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
        print(f"[*] Wiped previous {pkg}/")

    for stale in ("data", ".doc_qa_data", "tests", "__pycache__"):
        p = cwd / stale
        if p.exists() and p.is_dir():
            shutil.rmtree(p)
            print(f"[*] Wiped stale {stale}/")

    log_path = Path(f"/tmp/shakedown_{int(time.time())}.tui.log")
    start_time = time.time()

    driver = DrydockDriver(cwd, log_path)
    watcher = SessionWatcher(cwd, since=start_time)
    detector = FailureDetector()

    # Track failures with reasons
    fail_reasons: list[str] = []

    try:
        print("\n[*] Spawning drydock TUI...")
        driver.spawn()
        print(f"[*] TUI ready ({int(time.time() - start_time)}s). Sending prompt...")
        driver.type_message(prompt)
        time.sleep(2)

        last_progress_print = time.time()
        last_interrupt_at = 0.0

        while driver.alive():
            elapsed = time.time() - start_time
            if elapsed > MAX_SESSION_SECONDS:
                fail_reasons.append(
                    f"session exceeded {MAX_SESSION_SECONDS}s (real time)"
                )
                print(f"\n[FAIL] Hard timeout at {int(elapsed)}s")
                break

            # Drive pexpect to keep reading TUI output
            try:
                driver.child.expect(pexpect.TIMEOUT, timeout=2)
            except pexpect.EOF:
                print(f"\n[*] TUI exited at {int(elapsed)}s")
                break

            # If a tool permission dialog is up, auto-approve it.
            # Drydock blocks input on these prompts otherwise — same class
            # of bug as the trust dialog. Re-runs at every poll because
            # multiple permission prompts can fire across a session.
            if driver.dismiss_permission_prompts():
                print(f"  [{int(elapsed):4d}s] permission dialog auto-approved")

            # Refresh session log
            new_msgs = watcher.refresh()
            if new_msgs:
                detector.feed(watcher.messages)
                detector.update_useful_clock(watcher.messages)

            # Periodic progress
            if time.time() - last_progress_print > 10:
                last_progress_print = time.time()
                msg_n = len(watcher.messages)
                writes = sum(len(v) for v in detector.writes.values())
                loops = len(detector.write_loops_detected)
                silent = int(detector.silent_seconds())
                print(
                    f"  [{int(elapsed):4d}s] msgs={msg_n:3d} writes={writes:3d} "
                    f"loops={loops} silent={silent}s"
                )

            # ─── User-pain detectors ─────────────────────────────────
            # Inject "stop" interrupt on first detected loop, once per path
            loop_path = detector.loop_path_needs_interrupt()
            if loop_path and time.time() - last_interrupt_at > 5:
                print(
                    f"  [{int(elapsed):4d}s] LOOP DETECTED on '{loop_path}' "
                    f"— typing stop interrupt"
                )
                interrupt_msg = (
                    f"You are in a loop writing the same file. STOP. "
                    f"Move to the next file in the PRD."
                )
                try:
                    driver.type_message(interrupt_msg)
                    detector.interrupted_paths.add(loop_path)
                    detector.user_interrupts_sent.append(len(watcher.messages))
                    last_interrupt_at = time.time()
                except Exception as e:
                    print(f"  [WARN] failed to type interrupt: {e}")

            # Check if any interrupts were ignored
            detector.check_interrupt_obeyed(watcher.messages)

            # Has the model declared itself done? (text response, no tool call)
            if detector.model_declared_done(watcher.messages):
                if not getattr(self_state := type("S", (), {})(), "done_at", None):
                    pass  # placeholder
                # Track when "done" was first detected; give a grace period
                # for any final messages, then exit cleanly.
                if not hasattr(detector, "_done_at"):
                    detector._done_at = time.time()
                    print(
                        f"  [{int(elapsed):4d}s] model declared DONE — grace {DONE_GRACE_SECONDS}s"
                    )
                elif time.time() - detector._done_at > DONE_GRACE_SECONDS:
                    print(f"\n[*] Model finished (text response, no tool call)")
                    break

            # True dead silence: no new MESSAGES of any kind
            if detector.silent_seconds() > DEAD_SILENCE_SECONDS:
                fail_reasons.append(
                    f"dead silence for {int(detector.silent_seconds())}s "
                    f"(no new messages of any kind)"
                )
                print(f"\n[FAIL] Dead silence at {int(elapsed)}s")
                break

            # Hard fail conditions that should stop the test early
            if len(detector.search_replace_cascades) > 0:
                if "search_replace cascade" not in " ".join(fail_reasons):
                    fail_reasons.append(
                        f"search_replace failed {SEARCH_REPLACE_FAIL_CASCADE}+ times in a row"
                    )

    finally:
        driver.close()

    # ─── Final pass-criteria checks ──────────────────────────────────
    elapsed = time.time() - start_time
    detector.feed(watcher.messages)
    detector.check_interrupt_obeyed(watcher.messages)

    print()
    print("┌────────────────────────────────────────────────────")
    print("│ RESULTS")
    print(f"│   elapsed:     {int(elapsed)}s")
    print(f"│   messages:    {len(watcher.messages)}")
    print(f"│   total writes: {sum(len(v) for v in detector.writes.values())}")

    # 1. write loops
    print(f"│")
    print(f"│ ── pass criteria ──")
    if detector.write_loops_detected:
        print(f"│   ✗ write loops: {detector.write_loops_detected}")
        fail_reasons.append(
            f"write loops on: " + ", ".join(
                f"{p} ({n}x)" for p, n in detector.write_loops_detected
            )
        )
    else:
        print(f"│   ✓ no write loops")

    # 2. ignored interrupts
    if detector.ignored_after_interrupt:
        print(
            f"│   ✗ user interrupts ignored on: "
            f"{detector.ignored_after_interrupt}"
        )
        fail_reasons.append(
            f"model ignored user 'stop' interrupt on: "
            + ", ".join(detector.ignored_after_interrupt)
        )
    elif detector.user_interrupts_sent:
        print(f"│   ✓ user interrupts (sent {len(detector.user_interrupts_sent)}) obeyed")
    else:
        print(f"│   ✓ no interrupts needed")

    # 3. search_replace cascade
    if detector.search_replace_cascades:
        print(
            f"│   ✗ search_replace failure cascade at msg "
            f"{detector.search_replace_cascades}"
        )
    else:
        print(f"│   ✓ no search_replace cascade")

    # 4. package runs
    pkg_runs = False
    pkg_help_output = ""
    if pkg_dir.exists():
        try:
            res = subprocess.run(
                ["python3", "-m", pkg, "--help"],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=10,
            )
            pkg_runs = res.returncode == 0 and len(res.stdout.strip()) > 0
            pkg_help_output = (res.stdout + res.stderr)[:300]
        except Exception as e:
            pkg_help_output = f"(exception: {e})"
    if pkg_runs:
        print(f"│   ✓ python3 -m {pkg} --help works")
    else:
        print(f"│   ✗ python3 -m {pkg} --help broken")
        print(f"│       output: {pkg_help_output[:200]}")
        fail_reasons.append(f"python3 -m {pkg} --help did not work")

    # 5. session time
    if elapsed > MAX_SESSION_SECONDS:
        print(f"│   ✗ session exceeded {MAX_SESSION_SECONDS}s ({int(elapsed)}s)")
    else:
        print(f"│   ✓ session within time budget ({int(elapsed)}s / {MAX_SESSION_SECONDS}s)")

    # Verdict
    print("│")
    if fail_reasons:
        print("│ VERDICT: FAIL")
        for r in fail_reasons:
            print(f"│   - {r}")
    else:
        print("│ VERDICT: PASS")
    print("└────────────────────────────────────────────────────")
    print(f"\n  TUI capture log: {log_path}")
    if watcher.session_dir:
        print(f"  Session log:     {watcher.session_dir}/messages.jsonl")
    print()

    return 1 if fail_reasons else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True, help="Project working directory")
    parser.add_argument("--prompt", required=True, help="User prompt to type")
    parser.add_argument("--pkg", required=True, help="Expected package name")
    args = parser.parse_args()

    cwd = Path(args.cwd).resolve()
    if not cwd.exists():
        print(f"ERROR: cwd {cwd} does not exist", file=sys.stderr)
        return 2

    return run_test(cwd, args.prompt, args.pkg)


if __name__ == "__main__":
    sys.exit(main())

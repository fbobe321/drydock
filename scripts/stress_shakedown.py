#!/usr/bin/env python3
"""Stress test: drive the real drydock TUI through a long sequence of
feature-addition prompts from a file. Reuses the pexpect machinery from
shakedown_interactive.py so the model sees the same code paths a real
user would hit (NOT programmatic -p mode, NOT a custom harness).

Usage:
    python3 scripts/stress_shakedown.py \\
        --cwd /data3/drydock_test_projects/403_tool_agent \\
        --pkg tool_agent \\
        --prompts /tmp/stress_prompts.txt

Prompts file: one prompt per line. Lines starting with '#' are treated
as section headers (printed but not sent to the TUI). Blank lines are
skipped.

Unlike shakedown_interactive.py, this does NOT wipe the package dir
between prompts — the point is to accumulate feature additions on top
of a single build and see where the TUI starts to loop or drift.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# Import pexpect + the helpers we need from the existing harness
import pexpect

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shakedown_interactive import (  # noqa: E402
    DRYDOCK_BIN,
    SessionWatcher,
    drain_pty,
    send_prompt_and_confirm,
)


def _kill_orphan_drydock_tuis() -> int:
    """Kill any drydock TUI whose parent is init (ppid=1) — these are
    leaked children of previous stress runs. Returns the count killed.

    Only targets processes that:
    * ARE a drydock CLI invocation (matches drydock/bin/drydock),
    * have parent pid 1 (init — i.e. their real parent died),
    * are NOT this script's own pid.
    """
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,ppid,cmd"], text=True, timeout=5,
        )
    except Exception:
        return 0
    killed = 0
    my_pid = os.getpid()
    for line in out.splitlines()[1:]:
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        cmd = parts[2]
        if pid == my_pid or ppid != 1:
            continue
        if "drydock/bin/drydock" not in cmd:
            continue
        try:
            os.kill(pid, 15)  # SIGTERM
            killed += 1
        except Exception:
            pass
    if killed:
        time.sleep(2)
        # SIGKILL holdouts
        for line in out.splitlines()[1:]:
            parts = line.strip().split(None, 2)
            if len(parts) < 3:
                continue
            try:
                pid = int(parts[0]); ppid = int(parts[1])
            except ValueError:
                continue
            cmd = parts[2]
            if pid == my_pid or ppid != 1 or "drydock/bin/drydock" not in cmd:
                continue
            try:
                os.kill(pid, 0)
                os.kill(pid, 9)
            except Exception:
                pass
    return killed


def _parse_prompts(path: Path) -> list[tuple[str | None, str]]:
    """Return a list of (section_header | None, prompt_text)."""
    items: list[tuple[str | None, str]] = []
    current_section: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            current_section = line.lstrip("#").strip() or None
            continue
        items.append((current_section, line))
    return items


def _find_latest_checkpoint_session(
    cwd: Path, min_checkpoints: int = 0,
) -> tuple[Path, dict] | None:
    """Find a drydock checkpoint store whose work_tree matches cwd.

    When `min_checkpoints` is set, prefer the most recent store that has
    at least that many checkpoints; fall back to the most recent store
    overall if none qualify (caller's clamping logic will cope). This
    matters after a failed resume: the failed run creates its own (short)
    checkpoint store whose mtime beats the original long one. Without the
    filter we'd restore from the wrong store and lose 1000 prompts of
    progress.
    """
    base = Path.home() / ".drydock" / "checkpoints"
    if not base.is_dir():
        return None
    target = cwd.resolve()
    candidates: list[tuple[float, Path, dict]] = []
    for sess_dir in base.iterdir():
        if not sess_dir.is_dir():
            continue
        state = sess_dir / "state.json"
        if not state.is_file():
            continue
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        wt = data.get("work_tree", "")
        try:
            if Path(wt).resolve() == target:
                candidates.append((state.stat().st_mtime, sess_dir, data))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    if min_checkpoints > 0:
        qualified = [c for c in candidates
                     if len(c[2].get("checkpoints", [])) >= min_checkpoints]
        if qualified:
            return qualified[0][1], qualified[0][2]
    return candidates[0][1], candidates[0][2]


def _restore_checkpoint_to_step(session_dir: Path, state_data: dict,
                                cwd: Path, step: int) -> dict:
    """Use git directly to restore cwd files to the state right after
    step N completed.

    Step N nominally maps to checkpoint index N-1 (one checkpoint per
    completed user turn). In practice harness retries and resumes can
    cause drift, so we clamp: if step exceeds what the checkpoint store
    holds, we fall back to the LATEST checkpoint and let the caller
    skip the requested number of prompts. This is the right semantic
    for "I was at step N, resume there" — the work-tree is restored to
    the most recent state we have, and the harness picks up from N+1.
    Returns the chosen checkpoint dict.
    """
    checkpoints = state_data.get("checkpoints", [])
    if not checkpoints:
        raise SystemExit(
            f"checkpoint store {session_dir} has no checkpoints"
        )
    cp_index = step - 1
    if cp_index < 0:
        raise SystemExit(f"step {step} must be >= 1")
    if cp_index >= len(checkpoints):
        # Drift case — clamp to latest checkpoint and warn.
        cp_index = len(checkpoints) - 1
        cp = checkpoints[cp_index]
        print(
            f"  NOTE: step {step} > {len(checkpoints)} checkpoints "
            f"available; clamping to latest (cp #{cp_index} "
            f"\"{cp['label'][:50]}\")."
        )
    else:
        cp = checkpoints[cp_index]

    git_dir = session_dir / "repo.git"
    if not (git_dir / "HEAD").is_file():
        raise SystemExit(
            f"checkpoint repo at {git_dir} is missing or invalid"
        )
    env = {
        **os.environ,
        "GIT_DIR": str(git_dir),
        "GIT_WORK_TREE": str(cwd),
        "GIT_TERMINAL_PROMPT": "0",
    }
    # read-tree --reset -u rewrites the index AND the work-tree to match
    # the snapshot tree. Untracked files are left alone.
    subprocess.run(
        ["git", "read-tree", "--reset", "-u", cp["commit"]],
        env=env, check=True, capture_output=True, timeout=60,
    )
    return cp


# Matches drydock's own circuit-breaker banner. Example:
#   "[Stopping: 70+ API errors. The model cannot process this request.
#    Try /compact or /clear to free context.]"
# The TUI redraws this banner every turn once it's tripped. Without
# /clear it never clears, and every typed prompt is silently dropped.
_API_ERROR_BANNER_RE = re.compile(r"Stopping:\s*\d+\+\s*API errors",
                                  re.IGNORECASE)


# Window we scan for the banner. Each banner is ~4.6 KB of ANSI and the
# TUI may issue tens of KB of post-banner cursor-move redraws (spinner,
# idle flicker) that push the last banner off a small tail. Empirically
# 25 KB/redraw-burst is typical, so 256 KB covers several minutes of
# spinner noise and still catches a recent banner. If even this overflows
# on some future TUI rewrite, we should switch to tracking the last-seen
# banner byte-offset instead of rescanning.
_BANNER_SCAN_WINDOW = 256 * 1024


def _tui_has_api_error_banner(child: pexpect.spawn) -> bool:
    """True if drydock is showing its 'N+ API errors' circuit breaker.

    Reads the TUI's on-disk PTY log (via `child.logfile_read.name`) rather
    than `child.before` / `child.buffer`, because drain_pty truncates the
    in-memory copies to a 4KB tail — a redraw trivially pushes the banner
    off the tail. The on-disk log is append-only; we scan the last
    `_BANNER_SCAN_WINDOW` bytes for the banner text.
    """
    logfile = getattr(child, "logfile_read", None)
    path_str = getattr(logfile, "name", None)
    if path_str:
        try:
            p = Path(path_str)
            size = p.stat().st_size
            with p.open("rb") as f:
                f.seek(max(0, size - _BANNER_SCAN_WINDOW))
                tail = f.read().decode("utf-8", errors="replace")
            return bool(_API_ERROR_BANNER_RE.search(tail))
        except Exception:
            pass
    # Fallback: in-memory buffer (may be truncated).
    before = getattr(child, "before", "") or ""
    buf = getattr(child, "buffer", "") or ""
    return bool(_API_ERROR_BANNER_RE.search(before + buf))


def _send_clear_and_reset_watcher(child: pexpect.spawn,
                                  watcher: SessionWatcher,
                                  settle_seconds: float = 3.0) -> None:
    """Interrupt any in-flight turn with ESC, type /clear, then repoint
    the watcher at the new session dir. Shared by every-N-prompts reset,
    API-error recovery, and the consecutive-SKIP force-reset path.

    ESC-first matters because drydock's TUI explicitly lists "esc to
    interrupt" on its thinking spinner. Without it, /clear issued while
    the model is mid-thought may not register — the input component is
    inactive until the turn yields.
    """
    from shakedown_interactive import type_message
    try:
        child.send("\x1b")  # ESC — interrupts current turn if any
        time.sleep(0.6)
    except Exception:
        pass
    type_message(child, "/clear")
    time.sleep(settle_seconds)
    watcher.session_dir = None
    watcher.since = time.time()
    watcher.messages.clear()
    if hasattr(watcher, "_reset_offsets"):
        watcher._reset_offsets()
    for _ in range(30):
        if watcher.find_session() is not None:
            break
        time.sleep(1)


def _recover_if_api_error_banner(child: pexpect.spawn,
                                 watcher: SessionWatcher) -> bool:
    """If the TUI is stuck on the API-errors banner, send /clear and
    wait for a fresh session. Returns True if recovery was attempted.

    Without this, the original 1658-prompt stress run wedged at ~1100
    and every subsequent prompt SKIPped because the harness was typing
    into a circuit-broken TUI that wasn't reading stdin.
    """
    from shakedown_interactive import drain_pty as _drain
    _drain(child, seconds=1.0)
    has_banner = _tui_has_api_error_banner(child)
    # Diagnostic: every call prints its result so we can audit whether
    # the detector is firing when it should. Cheap (one line per iter).
    logfile = getattr(child, "logfile_read", None)
    path_str = getattr(logfile, "name", None)
    log_size = 0
    if path_str:
        try:
            log_size = Path(path_str).stat().st_size
        except Exception:
            pass
    print(f"          [rec-check] banner={has_banner} "
          f"log_size={log_size}", flush=True)
    if not has_banner:
        return False
    print("          RECOVER: TUI shows drydock's API-errors banner; "
          "sending /clear and rebinding watcher to new session",
          flush=True)
    _send_clear_and_reset_watcher(child, watcher, settle_seconds=5.0)
    return True


# Set by SIGUSR1 when stress_watcher's actuator decides the TUI is
# unrecoverably wedged. Checked once per main-loop iteration (between
# prompts, not inside pexpect calls, to avoid EINTR surprises). The
# next iteration kills the current child, spawns a fresh drydock TUI,
# and rebinds the watcher to the new session dir.
_recycle_requested = False


def _request_tui_recycle(_signum: int = 0, _frame: object = None) -> None:
    global _recycle_requested
    _recycle_requested = True


def _spawn_tui_child(log_path: Path, cwd: Path,
                     env: dict[str, str]) -> pexpect.spawn:
    """Spawn a fresh drydock TUI child with the stress harness's standard
    settings. Extracted from run() so the recycle path uses identical
    spawn configuration. Appends to the existing log (don't wipe the
    history — the watcher is polling it and the prior content is useful
    for post-mortem)."""
    child = pexpect.spawn(DRYDOCK_BIN, encoding="utf-8", timeout=5,
                          maxread=100000, env=env, cwd=str(cwd))
    child.logfile_read = open(log_path, "a", buffering=1)
    child.expect([r">", r"Drydock", r"┌"], timeout=30)
    time.sleep(2)
    # Auto-dismiss trust dialog — same as the initial spawn path.
    try:
        if "Trust this folder" in (child.before or ""):
            child.send("\x1b[D")
            time.sleep(0.3)
            child.send("\r")
            time.sleep(2)
    except Exception:
        pass
    return child


def _recycle_tui_child(old_child: pexpect.spawn,
                       watcher: SessionWatcher,
                       log_path: Path, cwd: Path,
                       env: dict[str, str]) -> pexpect.spawn:
    """Kill the current TUI child and spawn a fresh one. Called when
    _recycle_requested is set via SIGUSR1 from stress_watcher, or from
    the harness itself if it decides the TUI is unrecoverable. Returns
    the new child."""
    old_pid = getattr(old_child, "pid", "?")
    print(f"\n┈┈┈ RECYCLE-TUI: killing child PID {old_pid} and "
          f"respawning a fresh drydock ┈┈┈", flush=True)
    try:
        if old_child.isalive():
            old_child.sendcontrol("c")
            time.sleep(0.5)
        if old_child.isalive():
            old_child.terminate(force=True)
    except Exception:
        pass
    # Close old logfile handle so the new child can reopen cleanly
    try:
        lf = getattr(old_child, "logfile_read", None)
        if lf is not None and hasattr(lf, "close"):
            lf.close()
    except Exception:
        pass

    new_child = _spawn_tui_child(log_path, cwd, env)
    # Rebind the watcher to the new session dir drydock will create.
    watcher.session_dir = None
    watcher.since = time.time()
    watcher.messages.clear()
    if hasattr(watcher, "_reset_offsets"):
        watcher._reset_offsets()
    for _ in range(30):
        if watcher.find_session() is not None:
            break
        time.sleep(1)
    print(f"          recycle complete: new child PID "
          f"{getattr(new_child, 'pid', '?')}", flush=True)
    return new_child


def _idle_wait(child: pexpect.spawn, watcher: SessionWatcher,
               prev_msgs: int, max_seconds: float = 180.0,
               quiet_seconds: float = 8.0) -> dict:
    """Wait until the TUI is idle (no new messages for `quiet_seconds`)
    or max_seconds elapsed. Returns a small stats dict."""
    deadline = time.time() + max_seconds
    last_change = time.time()
    last_count = watcher.refresh()
    while time.time() < deadline:
        drain_pty(child, seconds=1.0)
        cur = watcher.refresh()
        if cur > last_count:
            last_count = cur
            last_change = time.time()
        elif (time.time() - last_change) > quiet_seconds and cur > prev_msgs:
            # idle after producing something
            break
        time.sleep(0.5)
    return {
        "msgs_after": last_count,
        "msgs_delta": last_count - prev_msgs,
        "timed_out": time.time() >= deadline,
    }


def _wait_until_tui_ready(child: pexpect.spawn, watcher: SessionWatcher,
                          max_seconds: float = 900.0,
                          quiet_seconds: float = 10.0) -> bool:
    """Wait for the TUI to be truly idle (ready to accept input).

    The TUI drops keystrokes while busy. After a long-running turn, we
    need to wait for it to stop producing output AND stop writing to
    the session log before typing the next prompt.

    Returns True if TUI appeared idle within the window, False if we
    hit max_seconds without seeing quiescence.
    """
    deadline = time.time() + max_seconds
    last_count = watcher.refresh()
    last_change = time.time()
    while time.time() < deadline:
        drain_pty(child, seconds=2.0)
        cur = watcher.refresh()
        if cur > last_count:
            last_count = cur
            last_change = time.time()
        if (time.time() - last_change) > quiet_seconds:
            return True
        time.sleep(1.0)
    return False


def run(cwd: Path, pkg: str, prompts_file: Path, max_per_prompt: float,
        report_every: int, resume_from_step: int = 0) -> int:
    print(f"\n{'=' * 60}")
    print(f"  STRESS SHAKEDOWN: {pkg}")
    print(f"  cwd:     {cwd}")
    print(f"  prompts: {prompts_file}")
    if resume_from_step:
        print(f"  resume:  from step {resume_from_step + 1}")
    print(f"{'=' * 60}\n")

    items = _parse_prompts(prompts_file)
    prompts_only = [(s, p) for s, p in items]
    total = len(prompts_only)
    print(f"Loaded {total} prompts.\n")

    skip_count = 0  # for --resume-from-step display

    if resume_from_step:
        # Find the most recent drydock checkpoint store for this cwd
        # that actually has enough checkpoints to reach resume_from_step.
        found = _find_latest_checkpoint_session(
            cwd, min_checkpoints=resume_from_step,
        )
        if found is None:
            print(f"ERROR: no checkpoint store found for cwd={cwd}.")
            print("       (drydock must have run in this dir before with v2.6.125+)")
            return 2
        sess_dir, state_data = found
        print(f"  checkpoint store: {sess_dir.name}")
        cp = _restore_checkpoint_to_step(sess_dir, state_data, cwd,
                                         resume_from_step)
        print(f"  restored to checkpoint {cp['index']} ({cp['commit'][:8]})")
        print(f"  label: {cp['label'][:80]!r}")
        print(f"  → resuming at step {resume_from_step + 1}\n")
        skip_count = resume_from_step
        prompts_only = prompts_only[resume_from_step:]
    else:
        # Fresh start from step 1: wipe everything except the fixtures
        # needed to drive the build. Previous policy was "accumulate so
        # the stress adds to an existing build"; in practice that meant
        # every fresh run carried 200+ files of partially-written docs
        # from prior runs, and drydock would spend its first turns
        # exploring that junk instead of building the package. Clean
        # slate each run → reproducible, faster start, no cross-run
        # contamination.
        _FIXTURES_TO_KEEP = {
            "PRD.master.md", "PRD.md", "AGENTS.md", "functional_tests.sh",
        }
        for entry in cwd.iterdir():
            if entry.name in _FIXTURES_TO_KEEP:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                try:
                    entry.unlink()
                except OSError:
                    pass
        master = cwd / "PRD.master.md"
        target = cwd / "PRD.md"
        if master.exists():
            shutil.copy2(master, target)
        kept = sorted(p.name for p in cwd.iterdir())
        print(f"  fresh-start wipe: kept {kept}")

    log_path = Path(f"/tmp/stress_shakedown_{int(time.time())}.tui.log")
    print(f"  TUI log: {log_path}\n")

    # First scan for orphan drydock TUIs from prior killed runs — they
    # starve vLLM and make every subsequent stress run degrade. Orphans
    # have ppid=1 (init adopted them) and run the drydock entrypoint.
    _killed = _kill_orphan_drydock_tuis()
    if _killed:
        print(f"  cleaned {_killed} orphan drydock TUI(s) from prior runs")

    start_time = time.time()
    env = {
        **os.environ,
        "TERM": "xterm-256color",
        "COLUMNS": "120",
        "LINES": "40",
        # Stop drydock's agent_loop from auto-appending "Continue." on
        # text-only assistant turns — for doc-writing stress prompts the
        # default behavior is an infinite loop (same answer regenerated
        # each time the Continue prod arrives). See
        # drydock/core/agent_loop.py::_sanitize_message_ordering.
        "DRYDOCK_AUTO_CONTINUE_DISABLE": "1",
    }
    # Truncate any stale log file at this path (from a prior run) so we
    # start fresh. _spawn_tui_child opens in append mode for the recycle
    # path; this initial open wipes it first.
    open(log_path, "w").close()
    child = _spawn_tui_child(log_path, cwd, env)

    # Critical: make sure the TUI child dies with us. Three layers so
    # no exit path (clean return, exception, SIGTERM, Ctrl+C) leaves
    # an orphan. SIGKILL against the harness itself still can't be
    # caught, but at that point the whole process tree is going anyway.
    # Closure captures `child` by name — the reference, not value — so
    # after a recycle the cleanup operates on the CURRENT child.
    def _cleanup_child(*_args) -> None:
        try:
            if child.isalive():
                child.sendcontrol("c")
                time.sleep(0.5)
            if child.isalive():
                child.terminate(force=True)
        except Exception:
            pass
    import atexit as _atexit
    _atexit.register(_cleanup_child)
    for _sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(_sig, lambda *a: (_cleanup_child(), os._exit(1)))
        except Exception:
            pass
    # SIGUSR1 from stress_watcher = "recycle the TUI child on the next
    # iteration boundary". The handler only sets a flag; the recycle
    # itself happens in the main loop between prompts (avoids EINTR
    # inside active pexpect calls).
    try:
        signal.signal(signal.SIGUSR1, _request_tui_recycle)
    except Exception:
        pass

    watcher = SessionWatcher(cwd, since=start_time)
    watcher.refresh()

    session_dir = None
    # Give the TUI a moment to create its session dir.
    for _ in range(30):
        watcher.refresh()
        if watcher.session_dir is not None:
            session_dir = watcher.session_dir
            break
        time.sleep(1)
    if session_dir is None:
        print("  WARN: no session dir created in 30s; continuing anyway")
    else:
        print(f"  session: {session_dir.name}\n")

    results: list[dict] = []
    accepted = 0
    skipped = 0
    timed_out = 0

    cur_section: str | None = None
    SESSION_RESET_EVERY = 15  # /clear every N prompts to bound context
    # Number of consecutive SKIPs that triggers an ESC+/clear force
    # reset. This catches stuck states the banner detector misses:
    # "admiral directive + no-tool-call" loops, silent model hangs on
    # huge context, spinner-for-20-minutes etc. — any case where the
    # harness can't get prompts through for multiple iterations in a
    # row means drydock is wedged regardless of whether it's showing
    # the API-errors banner or not.
    MAX_CONSECUTIVE_SKIPS_BEFORE_RESET = 2
    consecutive_skips = 0
    # When resuming, step counter starts at the resumed step + 1 so the
    # printed log lines stay aligned with the original run.
    for raw_i, (section, prompt) in enumerate(prompts_only, start=1):
        i = raw_i + skip_count
        if section != cur_section:
            cur_section = section
            if section:
                print(f"\n┈┈┈ {section} ┈┈┈")

        # Watcher-triggered recycle: stress_watcher.py sends SIGUSR1 when
        # it detects unrecoverable TUI wedge (memory bloat, pexpect-buffer-
        # leak fingerprint, prolonged skip cluster). Flag is checked here
        # so recycling happens cleanly between iterations, not mid-expect.
        global _recycle_requested
        if _recycle_requested:
            _recycle_requested = False
            child = _recycle_tui_child(child, watcher, log_path, cwd, env)
            consecutive_skips = 0

        # Adversarial-code-review pattern from asdlc.io: every N user
        # prompts, reset the session so context stays bounded.
        if i > 1 and (i - 1) % SESSION_RESET_EVERY == 0:
            print(f"\n┈┈┈ session reset (after {i - 1} prompts) ┈┈┈")
            _send_clear_and_reset_watcher(child, watcher)
            consecutive_skips = 0  # periodic reset also clears stuck state

        # Force reset when drydock has silently swallowed multiple
        # prompts. Happens when the model is stuck in an admiral-
        # directive loop with no tool calls, or thinking for 20+
        # minutes on a single turn — both cases dodge the banner
        # detector below because drydock never tripped its own
        # circuit breaker.
        if consecutive_skips >= MAX_CONSECUTIVE_SKIPS_BEFORE_RESET:
            print(f"          FORCE-RESET: {consecutive_skips} "
                  f"consecutive SKIPs; ESC + /clear to unstick",
                  flush=True)
            _send_clear_and_reset_watcher(child, watcher,
                                          settle_seconds=5.0)
            consecutive_skips = 0

        # Recover from drydock's API-errors circuit breaker if the
        # previous turn tripped it. /clear between every prompt is
        # too aggressive, but /clear WHEN we detect the banner is cheap
        # and is the only thing that lets the run keep moving.
        if _recover_if_api_error_banner(child, watcher):
            consecutive_skips = 0

        # Wait for TUI to be truly idle before typing next prompt.
        # If we type while drydock is still working on the prior turn,
        # keystrokes get dropped silently.
        if not _wait_until_tui_ready(child, watcher, max_seconds=900.0,
                                     quiet_seconds=10.0):
            print(f"          NOTE: TUI never went idle in 15min — "
                  f"typing anyway, may get dropped")

        prev_msgs = watcher.refresh()
        prev_writes = watcher.count_writes()
        print(f"\n[{i:>3}/{total}] {prompt[:80]}{'...' if len(prompt) > 80 else ''}")

        ok = send_prompt_and_confirm(child, prompt, watcher,
                                     max_retries=3, wait_per_retry=120.0)
        if not ok:
            print(f"          SKIP: TUI did not accept after 3 retries")
            skipped += 1
            consecutive_skips += 1
            results.append({"i": i, "prompt": prompt[:60], "status": "skipped"})
            continue
        accepted += 1
        consecutive_skips = 0

        stats = _idle_wait(child, watcher, prev_msgs,
                           max_seconds=max_per_prompt)
        delta_msgs = stats["msgs_delta"]
        delta_writes = watcher.count_writes() - prev_writes
        tag = "TIMEOUT" if stats["timed_out"] else "done"
        timed_out += 1 if stats["timed_out"] else 0
        print(f"          {tag:>7}: +{delta_msgs} msgs, +{delta_writes} writes "
              f"(total msgs={stats['msgs_after']})")
        results.append({
            "i": i, "prompt": prompt[:60], "status": tag,
            "delta_msgs": delta_msgs, "delta_writes": delta_writes,
        })

        if i % report_every == 0:
            print(f"\n─── PROGRESS at prompt {i}/{total} ───")
            print(f"  accepted:       {accepted}")
            print(f"  skipped:        {skipped}")
            print(f"  timed_out:      {timed_out}")
            print(f"  total msgs:     {stats['msgs_after']}")
            print(f"  total writes:   {watcher.count_writes()}")
            print(f"  elapsed:        {int(time.time() - start_time)}s")
            if session_dir:
                print(f"  session:        {session_dir.name}")

    elapsed = int(time.time() - start_time)
    print(f"\n{'=' * 60}")
    print(f"  STRESS RUN COMPLETE")
    print(f"  elapsed:       {elapsed}s ({elapsed // 60}m)")
    print(f"  prompts:       {total}")
    print(f"  accepted:      {accepted}")
    print(f"  skipped:       {skipped}")
    print(f"  timed_out:     {timed_out}")
    print(f"  total msgs:    {watcher.count_writes() if False else watcher.refresh()}")
    print(f"  total writes:  {watcher.count_writes()}")
    if session_dir:
        print(f"  session:       {session_dir.name}")
    print(f"  TUI log:       {log_path}")
    print(f"{'=' * 60}\n")

    try:
        child.sendcontrol("c")
        time.sleep(1)
        child.close()
    except Exception:
        pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Stress test drydock TUI with "
                                              "a long sequence of feature prompts.")
    ap.add_argument("--cwd", required=True, type=Path)
    ap.add_argument("--pkg", required=True)
    ap.add_argument("--prompts", required=True, type=Path,
                    help="Text file with one prompt per line")
    ap.add_argument("--max-per-prompt", type=float, default=180.0,
                    help="Max seconds to wait per prompt (default 180)")
    ap.add_argument("--report-every", type=int, default=20,
                    help="Print a progress snapshot every N prompts")
    ap.add_argument(
        "--resume-from-step", type=int, default=0, metavar="N",
        help=("Restore the project files from the most recent drydock "
              "checkpoint matching --cwd to the state right after step "
              "N completed, then continue typing from step N+1. The "
              "next drydock TUI starts with a fresh conversation; only "
              "the work-tree is restored. Requires drydock >=2.6.125 "
              "to have run in --cwd at least once."))
    args = ap.parse_args()

    if not args.cwd.is_dir():
        print(f"ERROR: --cwd not a directory: {args.cwd}")
        return 2
    if not args.prompts.is_file():
        print(f"ERROR: --prompts not a file: {args.prompts}")
        return 2
    if args.resume_from_step < 0:
        print(f"ERROR: --resume-from-step must be >= 0")
        return 2

    return run(args.cwd, args.pkg, args.prompts,
               args.max_per_prompt, args.report_every,
               resume_from_step=args.resume_from_step)


if __name__ == "__main__":
    sys.exit(main())

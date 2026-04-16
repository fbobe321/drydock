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
import os
import shutil
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
        report_every: int) -> int:
    print(f"\n{'=' * 60}")
    print(f"  STRESS SHAKEDOWN: {pkg}")
    print(f"  cwd:     {cwd}")
    print(f"  prompts: {prompts_file}")
    print(f"{'=' * 60}\n")

    items = _parse_prompts(prompts_file)
    prompts_only = [(s, p) for s, p in items]
    total = len(prompts_only)
    print(f"Loaded {total} prompts.\n")

    # Reset package dir + stale stuff ONCE, at the very start.
    master = cwd / "PRD.master.md"
    target = cwd / "PRD.md"
    if master.exists():
        shutil.copy2(master, target)
    pkg_dir = cwd / pkg
    if pkg_dir.exists():
        print(f"  (fresh run — wiping {pkg}/)")
        shutil.rmtree(pkg_dir)
    for entry in cwd.iterdir():
        if entry.is_dir() and entry.name != pkg and entry.name.startswith(pkg.split("_")[0]):
            if entry.name not in ("test_docs", "test_data", "plugins", ".git"):
                shutil.rmtree(entry, ignore_errors=True)

    log_path = Path(f"/tmp/stress_shakedown_{int(time.time())}.tui.log")
    print(f"  TUI log: {log_path}\n")

    start_time = time.time()
    env = {**os.environ, "TERM": "xterm-256color", "COLUMNS": "120", "LINES": "40"}
    child = pexpect.spawn(DRYDOCK_BIN, encoding="utf-8", timeout=5,
                          maxread=100000, env=env, cwd=str(cwd))
    child.logfile_read = open(log_path, "w", buffering=1)
    child.expect([r">", r"Drydock", r"┌"], timeout=30)
    time.sleep(2)

    # Auto-dismiss trust dialog
    try:
        if "Trust this folder" in (child.before or ""):
            child.send("\x1b[D")
            time.sleep(0.3)
            child.send("\r")
            time.sleep(2)
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
    SESSION_RESET_EVERY = 25  # /clear every N prompts to bound context
    for i, (section, prompt) in enumerate(prompts_only, start=1):
        if section != cur_section:
            cur_section = section
            if section:
                print(f"\n┈┈┈ {section} ┈┈┈")

        # Adversarial-code-review pattern from asdlc.io: every N user
        # prompts, reset the session so context stays bounded. Just
        # /clear — DON'T send a preamble (early version did and the
        # model interpreted it as an investigation task, burning 30+
        # tool calls before the next real prompt could land). Next
        # user prompt will name the feature; model can run
        # `--list-tools` itself if it wants to check state.
        if i > 1 and (i - 1) % SESSION_RESET_EVERY == 0:
            print(f"\n┈┈┈ session reset (after {i - 1} prompts) ┈┈┈")
            from shakedown_interactive import type_message
            type_message(child, "/clear")
            time.sleep(3)  # let TUI drain the /clear command
            # Invalidate session cache: /clear creates a new session
            # dir; without resetting these, the watcher polls the
            # OLD session forever and reports "prompt not accepted"
            # for everything in the new batch.
            watcher.session_dir = None
            watcher.since = time.time()
            watcher.messages = []
            # Wait for the new session dir to appear before continuing.
            for _ in range(30):
                if watcher.find_session() is not None:
                    break
                time.sleep(1)

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
            results.append({"i": i, "prompt": prompt[:60], "status": "skipped"})
            continue
        accepted += 1

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
    args = ap.parse_args()

    if not args.cwd.is_dir():
        print(f"ERROR: --cwd not a directory: {args.cwd}")
        return 2
    if not args.prompts.is_file():
        print(f"ERROR: --prompts not a file: {args.prompts}")
        return 2

    return run(args.cwd, args.pkg, args.prompts,
               args.max_per_prompt, args.report_every)


if __name__ == "__main__":
    sys.exit(main())

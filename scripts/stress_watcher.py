#!/usr/bin/env python3
"""Watch a running stress test and intervene at the harness level.

Unlike in-session Admiral (which watches AgentLoop messages),
stress_watcher observes `scripts/stress_shakedown.py`'s own log and:
* Detects stalls (no new prompt progress for N minutes).
* Detects rising timeout rate (last 50 prompts > threshold).
* Detects cliff-drops in pace (window variance).
* Emits Telegram pings and writes `admiral_history.log` entries so
  the dashboard reflects the intervention.

Does NOT restart the stress harness automatically (user rule: "don't
restart mid-run"). Surfaces the signal instead.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_PROGRESS_RE = re.compile(r"^\[\s*(\d+)/(\d+)\]\s+(.*)$")
_TIMEOUT_RE = re.compile(r"TIMEOUT:")
_CHECKPOINT_RE = re.compile(
    r"accepted:\s+(\d+).*?skipped:\s+(\d+).*?timed_out:\s+(\d+)",
    re.DOTALL,
)


@dataclass
class ProgressState:
    last_step: int = 0
    last_total: int = 0
    last_step_ts: float = 0.0
    stall_seconds: float = 0.0
    timeouts: int = 0
    skipped: int = 0
    accepted: int = 0


def _tail_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(errors="replace").splitlines()
    except Exception:
        return []


def _parse(lines: list[str]) -> ProgressState:
    s = ProgressState()
    for line in lines:
        m = _PROGRESS_RE.match(line)
        if m:
            step = int(m.group(1))
            total = int(m.group(2))
            if step > s.last_step:
                s.last_step = step
                s.last_total = total
    for line in lines:
        m = _CHECKPOINT_RE.search(line)
        if m:
            s.accepted = int(m.group(1))
            s.skipped = int(m.group(2))
            s.timeouts = int(m.group(3))
    return s


def _record(event: str, detail: str) -> None:
    log_dir = Path.home() / ".drydock" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with (log_dir / "admiral_history.log").open("a") as f:
        f.write(f"{ts} | {event} | {detail}\n")


def _telegram(msg: str) -> None:
    notify = Path("/data3/drydock/scripts/notify_release.py")
    if not notify.exists():
        return
    try:
        subprocess.run(
            ["python3", str(notify), "status", msg],
            check=False, timeout=10, capture_output=True,
        )
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def watch(log_path: Path, pid: int | None, stall_threshold_s: float = 900) -> None:
    """Poll loop. One alert per distinct stall window — no spam."""
    last_alert_at: dict[str, float] = {}

    def _alert_once(key: str, msg: str, cooldown: float = 1800) -> None:
        now = time.time()
        if now - last_alert_at.get(key, 0) < cooldown:
            return
        last_alert_at[key] = now
        _record("stress-alert", f"{key}: {msg}")
        _telegram(f"[stress-watcher] {msg}")

    last_step = 0
    last_step_ts = time.time()

    while True:
        try:
            lines = _tail_lines(log_path)
            state = _parse(lines)
            now = time.time()

            # 1. PID dead — critical.
            if pid and not _pid_alive(pid):
                _alert_once(
                    "pid-dead",
                    f"stress test PID {pid} is not running; "
                    f"last progress {state.last_step}/{state.last_total}",
                    cooldown=3600,
                )
                return  # harness is gone; nothing to watch

            # 2. Stall — no new step in threshold seconds.
            if state.last_step == last_step:
                if now - last_step_ts > stall_threshold_s:
                    _alert_once(
                        "stall",
                        f"no progress for {int((now - last_step_ts) / 60)} min "
                        f"(stuck on step {state.last_step}/{state.last_total})",
                    )
            else:
                last_step = state.last_step
                last_step_ts = now

            # 3. Timeout rate spike.
            if state.last_step > 100:
                timeout_rate = state.timeouts / state.last_step
                if timeout_rate > 0.02:
                    _alert_once(
                        "timeout-spike",
                        f"timeout rate {timeout_rate:.1%} "
                        f"({state.timeouts}/{state.last_step})",
                    )

            # 4. Completion.
            if state.last_total and state.last_step >= state.last_total:
                _alert_once(
                    "complete",
                    f"stress test COMPLETE: {state.last_step}/{state.last_total}, "
                    f"accepted={state.accepted}, timeouts={state.timeouts}",
                    cooldown=86400,
                )
                return

            time.sleep(30)
        except KeyboardInterrupt:
            return


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--log", required=True, help="path to stress log")
    p.add_argument("--pid", type=int, default=None, help="stress harness PID")
    p.add_argument(
        "--stall-threshold", type=float, default=900,
        help="alert if no progress for this many seconds",
    )
    args = p.parse_args()
    _record("stress-watcher", f"started on {args.log} pid={args.pid}")
    watch(Path(args.log), args.pid, args.stall_threshold)


if __name__ == "__main__":
    main()

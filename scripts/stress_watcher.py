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
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_PROGRESS_RE = re.compile(r"^\[\s*(\d+)/(\d+)\]\s+(.*)$")
_TIMEOUT_RE = re.compile(r"^\s*TIMEOUT:")
_RETRY_RE = re.compile(r"\[retry \d+: prompt not accepted")
_SKIP_RE = re.compile(r"SKIP: TUI did not accept")
_CHECKPOINT_RE = re.compile(
    r"accepted:\s+(\d+).*?skipped:\s+(\d+).*?timed_out:\s+(\d+)",
    re.DOTALL,
)
# Harness diagnostic line: "[rec-check] banner=... log_size=... raw_md=N"
# raw_md > 0 means the TUI log contains unrendered markdown patterns,
# which the user perceives as a "big blob of text". See
# stress_shakedown.py::_count_raw_markdown_leakage.
_RAW_MD_RE = re.compile(r"\[rec-check\]\s+banner=\S+\s+log_size=\S+\s+raw_md=(\d+)")


@dataclass
class ProgressState:
    last_step: int = 0
    last_total: int = 0
    last_step_ts: float = 0.0
    stall_seconds: float = 0.0
    timeouts: int = 0
    skipped: int = 0
    accepted: int = 0
    # Recent-window degradation signals (counted from last 200 log lines).
    recent_retries: int = 0
    recent_skips: int = 0
    recent_timeouts: int = 0
    recent_prompts: int = 0
    # Raw-markdown leakage: count of rec-check lines in the recent window
    # where raw_md > 0 (TUI is rendering unformatted markdown as a blob).
    recent_md_leaks: int = 0
    recent_md_checks: int = 0
    recent_md_total: int = 0


def _tail_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(errors="replace").splitlines()
    except Exception:
        return []


def _rss_kb(pid: int | None) -> int:
    """Return RSS in KB for `pid` (0 if unknown/dead)."""
    if pid is None:
        return 0
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except Exception:
        return 0
    return 0


def _count_orphan_drydock_tuis() -> int:
    """Count drydock TUI processes whose parent is init (ppid=1).

    These are leaks from prior killed harnesses. They starve vLLM and
    are the #1 cause of the late-run degradation pattern we keep
    seeing. Phase-1 stress_watcher missed this because it only
    looked at the progress log, not the OS state.
    """
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,ppid,cmd"], text=True, timeout=3,
        )
    except Exception:
        return 0
    n = 0
    for line in out.splitlines()[1:]:
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            ppid = int(parts[1])
        except ValueError:
            continue
        if ppid == 1 and "drydock/bin/drydock" in parts[2]:
            n += 1
    return n


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
    # Recent-window degradation: count signals in the last 200 log
    # lines (≈ last 30-60 prompts depending on verbosity).
    tail = lines[-200:]
    for line in tail:
        if _RETRY_RE.search(line):
            s.recent_retries += 1
        elif _SKIP_RE.search(line):
            s.recent_skips += 1
        elif _TIMEOUT_RE.match(line):
            s.recent_timeouts += 1
        elif _PROGRESS_RE.match(line):
            s.recent_prompts += 1
        else:
            md_match = _RAW_MD_RE.search(line)
            if md_match:
                s.recent_md_checks += 1
                n = int(md_match.group(1))
                if n > 0:
                    s.recent_md_leaks += 1
                    s.recent_md_total += n
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


# Per-alert-key throttle for Telegram pings. admiral_history.log is
# still written unthrottled (dashboard needs the full signal), but the
# user's phone only buzzes once per 6h per key regardless of how many
# times the underlying condition keeps re-firing. This is what makes
# the degradation signal survivable — a stuck run used to send one
# Telegram per cooldown * N alert types, turning the phone into a
# degradation barometer nobody wants.
_TELEGRAM_THROTTLE_SECONDS = 6 * 3600
_telegram_last_sent: dict[str, float] = {}


def _telegram_throttled(key: str, msg: str) -> None:
    now = time.time()
    if now - _telegram_last_sent.get(key, 0) < _TELEGRAM_THROTTLE_SECONDS:
        return
    _telegram_last_sent[key] = now
    _telegram(msg)


# Actuation: ask the stress harness to kill+respawn its TUI child via
# SIGUSR1. The harness registers _request_tui_recycle as the handler;
# the flag is checked once per main-loop iteration. Rate-limited so a
# chronic degradation doesn't thrash.
_RECYCLE_COOLDOWN = 600
_last_recycle_request_at = 0.0


def _request_tui_recycle(harness_pid: int | None, reason: str) -> bool:
    """Send SIGUSR1 to the harness. Returns True if the signal was sent.
    Goes to admiral_history as a 'stress-action' entry (distinct event
    type from 'stress-alert' so the dashboard can show what the watcher
    actually DID vs. what it just warned about)."""
    global _last_recycle_request_at
    if harness_pid is None or not _pid_alive(harness_pid):
        return False
    now = time.time()
    if now - _last_recycle_request_at < _RECYCLE_COOLDOWN:
        return False
    try:
        os.kill(harness_pid, signal.SIGUSR1)
    except Exception as e:
        _record("stress-action",
                f"tui-recycle-failed: pid={harness_pid} err={e}")
        return False
    _last_recycle_request_at = now
    _record("stress-action",
            f"tui-recycle-requested: pid={harness_pid} reason={reason}")
    _telegram_throttled(
        "tui-recycle",
        f"[stress-watcher] asked harness PID {harness_pid} to recycle "
        f"TUI: {reason}",
    )
    return True


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def watch(log_path: Path, pid: int | None, stall_threshold_s: float = 900) -> None:
    """Poll loop. One alert per distinct stall window — no spam.

    Previously cooldown-only: every detector re-fired every 30 min for as
    long as the condition held, flooding admiral_history.log with dozens
    of copies of the same message. Now we additionally suppress re-alerts
    whose "quantity" hasn't changed meaningfully since the last fire —
    memory-bloat must grow ≥20 %, spike/skip/timeout must advance with
    new step progress.
    """
    last_alert_at: dict[str, float] = {}
    last_alert_qty: dict[str, float] = {}

    def _alert_once(key: str, msg: str, cooldown: float = 1800,
                    qty: float | None = None,
                    qty_growth_ratio: float = 0.2) -> None:
        now = time.time()
        if now - last_alert_at.get(key, 0) < cooldown:
            return
        if qty is not None:
            prev = last_alert_qty.get(key)
            # Re-fire only if the measurement has grown meaningfully.
            if prev is not None and qty <= prev * (1.0 + qty_growth_ratio):
                return
            last_alert_qty[key] = qty
        last_alert_at[key] = now
        _record("stress-alert", f"{key}: {msg}")
        _telegram_throttled(key, f"[stress-watcher] {msg}")

    last_step = 0
    last_step_ts = time.time()
    # Step at which spike/skip detectors last fired, so we don't re-count
    # the same stuck cluster as a "new" degradation.
    last_spike_step = -1
    last_skip_step = -1
    # Composite pexpect-buffer-leak fingerprint fires at most once per run.
    leak_fingerprint_fired = False

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

            # 4. Orphan drydock TUIs starving vLLM (the bug that
            # caused the p1066+ degradation cluster on 2026-04-18).
            orphans = _count_orphan_drydock_tuis()
            if orphans > 0:
                _alert_once(
                    "orphan-drydock-tuis",
                    f"{orphans} orphan drydock TUI(s) detected (ppid=1) — "
                    f"these starve vLLM and degrade the active stress run. "
                    f"Run: pkill-by-pid the orphans (NOT pkill-by-name).",
                    cooldown=1800,
                )

            # 5. Retry-rate spike — TUI input layer is choking even when
            # prompts technically still get through. Only fires on a new
            # cluster: if the step counter hasn't moved since the last
            # spike, the harness is in the same stuck window — rely on
            # the stall detector instead of re-alerting.
            retry_rate = 0.0
            if state.recent_prompts > 5:
                retry_rate = state.recent_retries / max(state.recent_prompts, 1)
                if retry_rate > 0.5 and state.last_step > last_spike_step:
                    last_spike_step = state.last_step
                    _alert_once(
                        "retry-spike",
                        f"retry rate {retry_rate:.0%} in last "
                        f"{state.recent_prompts} prompts "
                        f"({state.recent_retries} retries) — TUI input "
                        f"layer choking; check vLLM contention",
                        cooldown=1800,
                    )

            # 6. Skip cluster — harness is giving up on prompts entirely.
            # Same guard: only a NEW stuck-window should alert. 3+ skips
            # in a window also triggers an actuator: the harness's own
            # FORCE-RESET didn't unstick things, so we escalate to a full
            # TUI respawn via SIGUSR1.
            if state.recent_skips >= 2 and state.last_step > last_skip_step:
                last_skip_step = state.last_step
                _alert_once(
                    "skip-cluster",
                    f"{state.recent_skips} SKIP events in last "
                    f"{state.recent_prompts} prompts — harness is "
                    f"abandoning prompts after 3 retries; degradation "
                    f"is severe",
                    cooldown=1800,
                )
                if state.recent_skips >= 3:
                    _request_tui_recycle(
                        pid,
                        f"{state.recent_skips} skips in last "
                        f"{state.recent_prompts} prompts, harness FORCE-"
                        f"RESET insufficient",
                    )

            # 7. Harness memory bloat — pexpect + SessionWatcher leaks
            # observed at ~130MB/h on long runs. By 4GB, GC thrash in
            # the harness causes it to miss TUI idle windows → retries
            # and skips. Quantity-dedup: re-alert only if RSS has grown
            # ≥20 % since the last fire, to stop the every-30-min noise
            # floor once the operator has been notified.
            harness_rss_mb = _rss_kb(pid) // 1024
            if harness_rss_mb > 2500:
                _alert_once(
                    "harness-memory-bloat",
                    f"stress harness RSS {harness_rss_mb} MB — likely "
                    f"pexpect buffer leak (drain_pty truncates in newer "
                    f"builds; confirm via git log shakedown_interactive.py). "
                    f"Harness will start missing TUI idle windows.",
                    cooldown=3600, qty=float(harness_rss_mb),
                )
                # Bloat over 4GB is almost certainly unrecoverable
                # without respawning the TUI. Actuate.
                if harness_rss_mb > 4000:
                    _request_tui_recycle(
                        pid,
                        f"harness RSS {harness_rss_mb} MB, pexpect "
                        f"buffers likely unreclaimable mid-run",
                    )

            # 7b. Composite fingerprint: retry-spike + bloat + flat
            # progress is the exact pexpect-buffer-leak signature. One
            # actionable diagnosis beats three correlated symptoms.
            if (not leak_fingerprint_fired
                    and harness_rss_mb > 3000
                    and retry_rate > 0.5
                    and (now - last_step_ts) > 600):
                leak_fingerprint_fired = True
                _alert_once(
                    "pexpect-buffer-leak",
                    f"retry-spike + RSS={harness_rss_mb}MB + "
                    f"progress flat {int((now - last_step_ts) / 60)}min. "
                    f"Fingerprint of drain_pty pexpect-buffer bloat. "
                    f"Restart harness; stress_shakedown.py supports "
                    f"--resume-from-step N to continue from checkpoint.",
                    cooldown=86400,
                )
                # This composite is the highest-confidence "TUI is dead"
                # signal we have. Actuate immediately; the alert alone
                # was what failed us on the v1→v5 debug cycle.
                _request_tui_recycle(
                    pid, "pexpect-buffer-leak composite fingerprint",
                )

            # 7c. Raw-markdown leakage — the TUI's Markdown widget
            # failed to render, user sees unformatted `##` / `**bold**`
            # as a blob. Advisory signal only (no actuator); the fix
            # lives in the TUI, not something a recycle can repair.
            # Quantity-dedup: only re-alert if the total count grew
            # meaningfully since the last fire.
            if (state.recent_md_checks >= 10
                    and state.recent_md_leaks >= 3):
                leak_rate = state.recent_md_leaks / state.recent_md_checks
                _alert_once(
                    "raw-markdown-leakage",
                    f"TUI blob: {state.recent_md_leaks}/{state.recent_md_checks} "
                    f"recent rec-checks saw unrendered markdown "
                    f"(leak rate {leak_rate:.0%}, {state.recent_md_total} raw "
                    f"patterns total in window). Markdown widget is "
                    f"failing to render — look at AssistantMessage + "
                    f"AnsiMarkdown in cli/textual_ui/widgets/messages.py",
                    cooldown=1800,
                    qty=float(state.recent_md_total),
                )

            # 8. Completion.
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

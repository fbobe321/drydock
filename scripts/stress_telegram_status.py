#!/usr/bin/env python3
"""Compose a stress-test status snapshot and send it via Telegram.

Designed to be run hourly by cron OR by hand. Reports:
- Stress process state (running, dead, PID)
- Last accepted/skipped prompt counts
- Active session loop signals
- Latest drydock version installed
- Top-level health verdict
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

NOTIFY = Path("/data3/drydock/scripts/notify_release.py")
SESSION_DIR = Path("/home/bobef/.drydock/logs/session")


def _stress_log_path() -> Path | None:
    """Pick the most recent stress_v*.log."""
    candidates = sorted(Path("/tmp").glob("stress_v*.log"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _stress_pid() -> int | None:
    out = subprocess.run(
        ["ps", "-eo", "pid,cmd"], capture_output=True, text=True, check=False,
    ).stdout
    for line in out.splitlines():
        if "stress_shakedown.py" in line and "grep" not in line and " --cwd " in line:
            try:
                return int(line.split()[0])
            except (ValueError, IndexError):
                pass
    return None


def _parse_progress(log: Path) -> tuple[int, int, int, int, str]:
    """Return (last_accepted, accepted, skipped, total, latest_prompt_text)."""
    if not log.exists():
        return 0, 0, 0, 201, "<no log>"
    text = log.read_text(errors="replace")
    last_n = 0
    last_prompt = "<unknown>"
    accepted = skipped = 0
    total = 201
    for m in re.finditer(r"^\[\s*(\d+)/(\d+)\] (.+?)\.\.\.|^\[\s*(\d+)/(\d+)\] (.+)$", text, re.MULTILINE):
        n = int(m.group(1) or m.group(4))
        tot = int(m.group(2) or m.group(5))
        prompt = (m.group(3) or m.group(6) or "").strip()
        last_n = n
        total = tot
        last_prompt = prompt[:60]
    accepted = len(re.findall(r"^\s+done:", text, re.MULTILINE))
    skipped = len(re.findall(r"^\s+SKIP:", text, re.MULTILINE))
    return last_n, accepted, skipped, total, last_prompt


def _latest_session() -> Path | None:
    if not SESSION_DIR.exists():
        return None
    sessions = sorted(SESSION_DIR.glob("session_*"), key=lambda p: p.stat().st_mtime)
    return sessions[-1] if sessions else None


def _audit_session(s: Path) -> dict:
    msgs = s / "messages.jsonl"
    if not msgs.exists():
        return {}
    calls = 0
    unique_sigs: set[str] = set()
    last_consec = 0
    max_consec = 0
    prev_sig = None
    error_calls = 0
    total_results = 0
    for line in msgs.read_text(errors="replace").splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                sig = f"{fn.get('name', '?')}:{(fn.get('arguments') or '')[:200]}"
                unique_sigs.add(sig)
                calls += 1
                if sig == prev_sig:
                    last_consec += 1
                else:
                    if last_consec > max_consec:
                        max_consec = last_consec
                    last_consec = 1
                prev_sig = sig
        elif m.get("role") == "tool":
            total_results += 1
            content = str(m.get("content", "") or "")
            if "<tool_error>" in content or "user_cancellation" in content:
                error_calls += 1
    if last_consec > max_consec:
        max_consec = last_consec
    return {
        "calls": calls,
        "unique": len(unique_sigs),
        "dup_ratio": round(1 - len(unique_sigs) / calls, 2) if calls else 0,
        "max_consec": max_consec,
        "errors": error_calls,
        "results": total_results,
    }


def _drydock_version() -> str:
    try:
        out = subprocess.run(
            ["/home/bobef/miniforge3/envs/drydock/bin/python3", "-c",
             "import drydock; print(drydock.__version__)"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return out.stdout.strip() or "?"
    except Exception:
        return "?"


def main() -> int:
    log = _stress_log_path()
    pid = _stress_pid()
    last_n, accepted, skipped, total, last_prompt = _parse_progress(log) if log else (0, 0, 0, 0, "<no log>")
    sess = _latest_session()
    audit = _audit_session(sess) if sess else {}
    version = _drydock_version()

    state = "running" if pid else "DEAD"
    log_name = log.name if log else "n/a"
    sess_name = sess.name if sess else "n/a"

    health = "OK"
    if not pid:
        health = "STOPPED"
    elif audit.get("max_consec", 0) >= 12:
        health = f"LOOP({audit['max_consec']})"
    elif audit.get("dup_ratio", 0) >= 0.7:
        health = f"DUP({audit['dup_ratio']})"

    lines = [
        f"⚓ Stress status — {health}",
        f"v{version} · {state}{f' PID={pid}' if pid else ''}",
        f"prompts: {last_n}/{total} accepted={accepted} skipped={skipped}",
    ]
    if audit:
        lines.append(
            f"session: {audit['calls']} calls, {audit['unique']} unique, "
            f"dup={audit['dup_ratio']}, max-consec={audit['max_consec']}, "
            f"errors={audit['errors']}/{audit['results']}"
        )
    lines.append(f"latest prompt: {last_prompt}")
    msg = "\n".join(lines)

    print(msg)
    subprocess.run(
        ["/home/bobef/miniconda3/bin/python3", str(NOTIFY), "status", msg],
        check=False, timeout=10,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

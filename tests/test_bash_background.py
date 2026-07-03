"""tool_bash must support backgrounded processes (`cmd &`) — a server/daemon the
task wants to keep running — instead of hanging to the timeout then killing it.
Found via the kv-store-grpc tbench task (needs a background gRPC server)."""
from __future__ import annotations

import subprocess
import time

from drydock.tools import tool_bash


def _timed(cmd, timeout=8):
    t0 = time.monotonic()
    out = tool_bash({"command": cmd, "timeout": timeout}, {"cwd": "/tmp"})
    return time.monotonic() - t0, out


def test_backgrounded_command_returns_fast_and_survives():
    try:
        dt, out = _timed("sleep 12 & echo bg-started")
        assert dt < 3.0, f"backgrounded cmd hung ({dt:.1f}s) — should return promptly"
        assert "background" in out.lower()
        time.sleep(0.5)
        n = subprocess.run("pgrep -f 'sleep 12' | wc -l", shell=True,
                           capture_output=True, text=True).stdout.strip()
        assert int(n) >= 1, "backgrounded process was killed (should survive)"
    finally:
        subprocess.run("pkill -f 'sleep 12'", shell=True)


def test_normal_command_unaffected():
    dt, out = _timed("echo hello && echo world")
    assert "hello" in out and "world" in out and dt < 2.0
    assert "background" not in out.lower()


def test_foreground_hang_still_times_out():
    dt, out = _timed("sleep 30", timeout=3)
    assert "timed out" in out and dt < 6.0


def test_failing_command_still_reports_exit_code():
    _, out = _timed("false")
    assert "[exit code: 1]" in out

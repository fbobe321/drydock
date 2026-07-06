"""tool_bash must preserve partial output when the user presses STOP.
Without this fix, cancel.is_set() triggered a bare "[stopped by user]" return
that discarded all chunks accumulated before the stop — losing useful diagnostics
(test-run results, build output, etc.) that the model needs to continue.

Mirrors the timeout-partial-output behaviour already in test_bash_background.py.
"""
from __future__ import annotations

import threading
import time

from drydock.tools import tool_bash


def test_stop_preserves_partial_output():
    """Partial output printed before the cancel Event fires must appear in the
    tool result alongside the '[stopped by user]' marker."""
    cancel = threading.Event()

    # Fire cancel after a short delay so the command has time to emit output.
    def _fire():
        time.sleep(0.5)
        cancel.set()

    threading.Thread(target=_fire, daemon=True).start()
    out = tool_bash(
        {"command": "echo PARTIAL_LINE; echo second_line; sleep 30", "timeout": 20},
        {"cwd": "/tmp", "_cancel": cancel},
    )
    assert "stopped by user" in out
    assert "PARTIAL_LINE" in out, f"partial output missing from stop result: {out!r}"
    assert "output before stop" in out


def test_stop_with_no_output_returns_plain_marker():
    """If the command produces no output before the stop, the result is just
    '[stopped by user]' without a spurious empty block."""
    cancel = threading.Event()

    def _fire():
        time.sleep(0.05)
        cancel.set()

    threading.Thread(target=_fire, daemon=True).start()
    out = tool_bash(
        {"command": "sleep 30", "timeout": 20},
        {"cwd": "/tmp", "_cancel": cancel},
    )
    assert out == "[stopped by user]"

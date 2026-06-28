"""Critical VRAM/RAM protection (PRD §3.B): tool_bash must bound a command's
output DURING capture so a runaway/infinite-output command can't balloon memory
before the context-truncation cap applies."""
from __future__ import annotations

import time

from drydock.tools import tool_bash, _MAX_BASH_OUTPUT_BYTES


def test_infinite_output_is_capped_and_fast(tmp_path):
    cfg = {"cwd": str(tmp_path)}
    t0 = time.monotonic()
    out = tool_bash({"command": "yes"}, cfg)   # infinite stream
    elapsed = time.monotonic() - t0
    assert "truncated at" in out                      # capped, not dumped
    assert len(out) < _MAX_BASH_OUTPUT_BYTES + 500     # bounded memory
    assert elapsed < 20                                # killed promptly, no hang


def test_large_finite_output_is_capped(tmp_path):
    cfg = {"cwd": str(tmp_path)}
    # ~1 MB of output → must come back truncated near the cap, not 1 MB.
    out = tool_bash({"command": "head -c 1000000 /dev/zero | tr '\\0' 'a'"}, cfg)
    assert len(out) < _MAX_BASH_OUTPUT_BYTES + 500


def test_normal_output_untouched(tmp_path):
    cfg = {"cwd": str(tmp_path)}
    out = tool_bash({"command": "echo hello world"}, cfg)
    assert out == "hello world" and "truncated" not in out


def test_exit_code_still_reported(tmp_path):
    cfg = {"cwd": str(tmp_path)}
    out = tool_bash({"command": "echo oops; exit 3"}, cfg)
    assert "oops" in out and "exit code: 3" in out


def test_repetitive_output_is_collapsed(tmp_path):
    # PRD "protect context size": a repetitive dump must not eat the window.
    out = tool_bash({"command": "yes | head -c 2000000"}, {"cwd": str(tmp_path)})
    assert len(out) < 500                       # ~24k chars before the collapse
    assert "identical lines collapsed" in out


def test_collapse_preserves_distinct_lines(tmp_path):
    out = tool_bash({"command": "printf 'a\\nb\\nc\\n'"}, {"cwd": str(tmp_path)})
    assert out == "a\nb\nc"                      # nothing collapsed

"""Bash tool: longer default timeout (real builds/training/cracking need >30s)
and an offline-download hint so the model stops looping on failed fetches.
Both surfaced from terminal-bench failure analysis (2026-06-25): 16 tasks died
at the old 30s wall; 12 looped on downloads in an offline container."""
from __future__ import annotations

from drydock.tools import (
    _OFFLINE_HINT,
    _is_network_command,
    _looks_like_network_failure,
    tool_bash,
)


def test_default_timeout_is_generous():
    # A command that takes a couple seconds must NOT be killed (old default 30s
    # was fine here, but builds/training needed more — default is now 120s).
    import inspect
    src = inspect.getsource(tool_bash)
    assert 'params.get("timeout", 120)' in src
    assert "min(timeout * 4, 1800)" in src  # escalation cap raised 600 -> 1800


def test_network_command_detection():
    assert _is_network_command("pip install numpy")
    assert _is_network_command("uv pip install torch")
    assert _is_network_command("wget https://x/y.tar")
    assert _is_network_command("git clone https://github.com/a/b")
    assert _is_network_command("python3 -c \"from datasets import load_dataset; load_dataset('x')\"")
    assert not _is_network_command("python3 train.py")   # local compute
    assert not _is_network_command("pytest -q")
    assert not _is_network_command("ls -la")


def test_network_failure_detection():
    assert _looks_like_network_failure("Could not resolve host: pypi.org")
    assert _looks_like_network_failure("ConnectionError: Max retries exceeded")
    assert _looks_like_network_failure("Network is unreachable")
    assert not _looks_like_network_failure("AssertionError: 3 != 4")
    assert not _looks_like_network_failure("Build succeeded")


def test_offline_hint_only_on_failed_network_command():
    # real failed fetch → hint appended
    out = tool_bash({"command": "curl https://nonexistent.invalid.example/x"}, {"cwd": "/tmp"})
    assert _OFFLINE_HINT.strip()[:20] in out
    # plain successful command → no hint
    out2 = tool_bash({"command": "echo hi"}, {"cwd": "/tmp"})
    assert "OFFLINE" not in out2
    # a local command that fails for a NON-network reason → no offline hint
    out3 = tool_bash({"command": "python3 -c 'raise SystemExit(2)'"}, {"cwd": "/tmp"})
    assert "OFFLINE" not in out3

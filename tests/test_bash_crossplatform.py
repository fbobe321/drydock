"""tool_bash must run on Linux AND Windows. The Windows blocker was shell=True +
executable=bash → Popen builds `bash /c cmd` (cmd.exe syntax) which a real
bash.exe rejects. Fix: invoke `[bash, "-c", cmd]` explicitly; use taskkill on
Windows for the process-tree kill; setsid only on POSIX."""
from __future__ import annotations

import os
import subprocess

from drydock import tools
from drydock.tools import _detect_bash, _IS_WINDOWS, kill_process_group, tool_bash


def test_platform_flag_matches_os():
    assert _IS_WINDOWS == (os.name == "nt")


def test_detect_bash_returns_path_or_none():
    r = _detect_bash()
    assert r is None or (isinstance(r, str) and "bash" in r.lower())


def test_bash_invoked_as_argv_not_shell_c(monkeypatch):
    """When bash is available, Popen is called with [bash, '-c', cmd] and
    shell=False — never shell=True+executable (which breaks on Windows)."""
    if not tools._BASH_SHELL:
        return
    seen = {}
    real = subprocess.Popen

    def spy(popen_cmd, *a, **kw):
        seen["cmd"] = popen_cmd
        seen["shell"] = kw.get("shell")
        return real(popen_cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", spy)
    tool_bash({"command": "echo hi", "timeout": 5}, {"cwd": "/tmp"})
    assert seen["shell"] is False
    assert isinstance(seen["cmd"], list) and seen["cmd"][1] == "-c"
    assert seen["cmd"][0].lower().endswith(("bash", "bash.exe"))


def test_kill_process_group_none_is_safe():
    kill_process_group(None)  # must not raise


def test_command_still_runs_here():
    out = tool_bash({"command": "echo cross_platform_ok", "timeout": 5}, {"cwd": "/tmp"})
    assert "cross_platform_ok" in out

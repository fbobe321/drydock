"""Tests for the bash command safety denylist.

The contract: catastrophic, irreversible, no-legitimate-use commands are
refused; ordinary scoped destructive work passes through. False positives are
worse than the occasional miss, so the "allowed" cases below are load-bearing.
"""
from __future__ import annotations

import pytest

from drydock.bash_safety import dangerous_command
from drydock.tools import tool_bash


# ── blocked: catastrophic ──────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "rm -rf /*",
    "rm -fr /",
    "rm -rf ~",
    "rm -rf $HOME",
    "sudo rm -rf / --no-preserve-root",
    "rm -rf  /  ",
    "mkfs.ext4 /dev/sda1",
    "mkfs /dev/sdb",
    "dd if=/dev/zero of=/dev/sda bs=1M",
    "echo hi > /dev/sda",
    ":(){ :|:& };:",
    "chmod -R 000 /",
    "chown -R nobody /",
])
def test_catastrophic_commands_are_refused(cmd):
    reason = dangerous_command(cmd)
    assert reason is not None, f"should have blocked: {cmd}"


# ── allowed: real coding work, must NOT be blocked ─────────────────────────

@pytest.mark.parametrize("cmd", [
    "rm -rf build/",
    "rm -rf node_modules",
    "rm -rf ./dist",
    "rm -rf /tmp/v3_repro",          # scoped path under /tmp, not root itself
    "rm file.txt",
    "rm -f *.pyc",
    "git reset --hard HEAD",
    "dd if=input.img of=output.img",  # file to file, no device
    "python3 test.py",
    "pytest -q",
    "ls -la /",
    "cat /etc/hostname",
    "chmod +x script.sh",
    "chmod -R 755 build/",
    "find / -name '*.py'",            # reads root, deletes nothing
])
def test_legitimate_commands_pass_through(cmd):
    assert dangerous_command(cmd) is None, f"false positive on: {cmd}"


# ── integration: tool_bash returns a refusal instead of running ────────────

def test_tool_bash_refuses_without_running(tmp_path):
    out = tool_bash({"command": "rm -rf /"}, {"cwd": str(tmp_path)})
    assert out.startswith("REFUSED")
    assert "filesystem root" in out


def test_tool_bash_still_runs_safe_command(tmp_path):
    out = tool_bash({"command": "echo hello"}, {"cwd": str(tmp_path)})
    assert "hello" in out

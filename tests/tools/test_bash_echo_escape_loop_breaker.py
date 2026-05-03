"""Regression test: bash tool targeted hint for echo -e / printf escape loops.

Stress run on 2026-05-02 showed Gemma 4 looping on commands like:
    echo -e "name\\tage\\trole\\nAlice\\t30"
    printf "name\\tage\\trole\\nAlice\\t30"

The shell (often /bin/sh/dash) does not interpret \\t/\\n escape sequences
from echo -e (dash ignores -e), or backslash doubling in quoting eats the
escapes, so the output is a flat string like "nametagetrole". The model
sees the wrong output, re-runs identically, and the admiral has to fire
loop:bash interventions repeatedly.

Fix: in the dedup loop-breaker (3rd+ identical command+output), detect
echo -e and printf patterns with \\n/\\t in the command string and emit
a targeted hint (use $'...' quoting, python3 -c, or write_file) instead
of the generic "EDIT SOURCE CODE" message.
"""
from __future__ import annotations

import pytest

from tests.mock.utils import collect_result
from drydock.core.tools.base import BaseToolState, ToolPermission
from drydock.core.tools.builtins.bash import Bash, BashArgs, BashToolConfig


@pytest.fixture
def bash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = BashToolConfig()
    return Bash(config=config, state=BaseToolState())


@pytest.mark.asyncio
async def test_echo_escape_loop_breaker_fires_with_targeted_hint(bash):
    """3rd+ identical echo -e run gets escape-sequence hint, not generic one."""
    # Command that has \\t/\\n (literal backslash sequences, as model sends them)
    cmd = "echo -e \"name\\tage\\trole\\nAlice\\t30\""
    for _ in range(2):
        await collect_result(bash.run(BashArgs(command=cmd)))
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "escape" in result.stdout.lower() or "\\n" in result.stdout or "\\t" in result.stdout
    # Should NOT say "EDIT SOURCE CODE" (generic unhelpful hint)
    assert "EDIT SOURCE CODE" not in result.stdout
    # Should suggest alternatives
    assert any(s in result.stdout for s in ["$'", "python3", "write_file", "printf"])


@pytest.mark.asyncio
async def test_printf_escape_loop_breaker_fires_with_targeted_hint(bash):
    """3rd+ identical printf run with \\t gets escape-sequence hint."""
    cmd = "printf \"name\\tage\\trole\\nAlice\\t30\""
    for _ in range(2):
        await collect_result(bash.run(BashArgs(command=cmd)))
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "EDIT SOURCE CODE" not in result.stdout
    assert any(s in result.stdout for s in ["$'", "python3", "write_file", "escape"])


@pytest.mark.asyncio
async def test_heredoc_loop_breaker_still_works(bash, tmp_path):
    """The heredoc hint is not broken by the new echo-escape detection."""
    target = tmp_path / "out.py"
    cmd = f"cat << 'EOF' > {target}\nprint('hello')\nEOF"
    for _ in range(2):
        await collect_result(bash.run(BashArgs(command=cmd)))
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    # Heredoc hint should still mention reading the file
    assert "read" in result.stdout.lower() or "write_file" in result.stdout.lower()
    assert "EDIT SOURCE CODE" not in result.stdout


@pytest.mark.asyncio
async def test_generic_loop_breaker_for_non_echo_commands(bash):
    """Non-echo/printf loops still get the generic hint."""
    cmd = "python3 -c \"print('hello')\""
    for _ in range(2):
        await collect_result(bash.run(BashArgs(command=cmd)))
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "EDIT SOURCE CODE" in result.stdout

"""Regression test: bash tool targeted hint for empty ls/grep search loops.

Stress run on 2026-05-03 showed Gemma 4 looping on commands like:
    ls -F | grep "test_cli"
    ls -F | grep "test_race"
    grep -r "class " tool_agent/ | grep -v ".pyc"

When the target file/symbol doesn't exist the command returns empty stdout
with rc=0 every run.  The generic "EDIT SOURCE CODE" hint is confusing for
search commands; the model needs to know the thing it's looking for simply
does not exist yet and it should CREATE it.

Fix: in the dedup loop-breaker (3rd+ identical command+output), detect ls,
grep, find, and rg commands that returned empty stdout with rc=0 and emit a
targeted "nothing matched — stop searching, create the file" hint.
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
async def test_ls_grep_empty_loop_breaker(bash, tmp_path):
    """3rd+ identical ls | grep with empty result gets targeted hint."""
    cmd = 'ls -F | grep "test_cli"'
    for _ in range(2):
        await collect_result(bash.run(BashArgs(command=cmd)))
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "EDIT SOURCE CODE" not in result.stdout
    assert "does not exist" in result.stdout or "STOP" in result.stdout or "create" in result.stdout.lower()


@pytest.mark.asyncio
async def test_grep_r_empty_loop_breaker(bash, tmp_path):
    """3rd+ identical grep -r with empty result gets targeted hint."""
    cmd = 'grep -r "NonexistentClass" .'
    for _ in range(2):
        await collect_result(bash.run(BashArgs(command=cmd)))
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "EDIT SOURCE CODE" not in result.stdout
    assert "does not exist" in result.stdout or "STOP" in result.stdout or "create" in result.stdout.lower()


@pytest.mark.asyncio
async def test_non_empty_grep_not_affected(bash, tmp_path):
    """grep that returns non-empty output does not get the empty-search hint."""
    target = tmp_path / "sample.py"
    target.write_text("class Foo:\n    pass\n")
    cmd = f"grep -r 'class Foo' {tmp_path}"
    for _ in range(2):
        await collect_result(bash.run(BashArgs(command=cmd)))
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    # Generic loop-breaker fires, not the empty-search one
    assert "does not exist" not in result.stdout
    assert "EDIT SOURCE CODE" in result.stdout or "NOTICE" in result.stdout


@pytest.mark.asyncio
async def test_other_loop_still_generic(bash):
    """Non-search commands with empty output use generic hint, not empty-search."""
    cmd = "python3 -c 'pass'"
    for _ in range(2):
        await collect_result(bash.run(BashArgs(command=cmd)))
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    # Generic hint fires; empty-search hint should NOT
    assert "does not exist" not in result.stdout

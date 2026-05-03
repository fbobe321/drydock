"""Regression test: consecutive-empty-search cross-command loop-breaker.

The model semantic-loops by trying different search terms for a non-existent
target (each command is unique so the identical-hash check doesn't fire):
    ls -F | grep "test_cli"   → empty
    ls -F | grep "test_race"  → empty
    grep -r "bug_B" .         → empty
    ...

After 5 consecutive empty-stdout search commands, bash.py injects a
LOOP-BREAKER hint telling the model to stop searching and ask the user.
"""
from __future__ import annotations

import pytest

from tests.mock.utils import collect_result
from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.bash import Bash, BashArgs, BashToolConfig


@pytest.fixture
def bash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = BashToolConfig()
    return Bash(config=config, state=BaseToolState())


async def _run(bash, cmd: str) -> str:
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    return result.stdout


@pytest.mark.asyncio
async def test_consec_empty_search_triggers_at_5(bash):
    """After 5 different empty-stdout search commands, LOOP-BREAKER fires."""
    cmds = [
        'ls -F | grep "test_cli"',
        'ls -F | grep "test_race"',
        'grep -r "bug_B" . 2>/dev/null || true',
        'find . -name "test_h.py" 2>/dev/null',
        'ls -F | grep "component_g"',
    ]
    for cmd in cmds:
        result = await _run(bash, cmd)
    assert "LOOP-BREAKER" in result
    assert "consecutive search commands" in result


@pytest.mark.asyncio
async def test_consec_empty_search_resets_on_nonempty(bash, tmp_path):
    """Counter resets when a search returns non-empty output."""
    (tmp_path / "found.py").write_text("x = 1\n")
    cmds = [
        'ls -F | grep "test_cli"',
        'ls -F | grep "test_race"',
        'ls -F | grep "found"',  # this returns non-empty — resets counter
        'ls -F | grep "test_cli"',
        'ls -F | grep "test_race"',
    ]
    results = []
    for cmd in cmds:
        results.append(await _run(bash, cmd))
    # Should NOT have fired (counter reset at cmd 3)
    assert "LOOP-BREAKER" not in results[-1]


@pytest.mark.asyncio
async def test_non_search_commands_ignored(bash):
    """Non-search commands don't increment the empty-search counter."""
    cmds = [
        'ls -F | grep "test_cli"',
        'echo hello',
        'ls -F | grep "test_race"',
        'python3 --version',
        'ls -F | grep "component_g"',
    ]
    results = []
    for cmd in cmds:
        results.append(await _run(bash, cmd))
    assert "LOOP-BREAKER" not in results[-1]

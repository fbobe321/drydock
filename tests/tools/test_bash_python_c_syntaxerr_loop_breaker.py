"""Regression tests for the python3 -c SyntaxError loop-breaker.

The model often truncates multi-line inline scripts, gets a SyntaxError,
and retries with slightly different content.  Because each stderr differs
(different column/line), the hash-based dedup never fires.  The dedicated
cross-command counter catches this on the 2nd failure and redirects to
write_file.
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


@pytest.mark.asyncio
async def test_first_syntaxerr_passes_through(bash):
    """First python3 -c SyntaxError is returned normally (no loop-breaker yet)."""
    result = await collect_result(bash.run(BashArgs(command="python3 -c \"print('hello\"")))
    assert "LOOP-BREAKER" not in result.stdout


@pytest.mark.asyncio
async def test_second_syntaxerr_triggers_loop_breaker(bash):
    """Second python3 -c SyntaxError (different command) triggers the loop-breaker."""
    # Truncated strings produce SyntaxError (unterminated string literal)
    await collect_result(bash.run(BashArgs(command="python3 -c \"x = {'key': 'val\"")))
    result = await collect_result(bash.run(BashArgs(command="python3 -c \"y = {'k2': 'v2\"")))
    assert "LOOP-BREAKER" in result.stdout
    assert "write_file" in result.stdout.lower() or "Write the script" in result.stdout


@pytest.mark.asyncio
async def test_loop_breaker_only_fires_on_syntaxerr(bash):
    """ImportError (no SyntaxError) does not increment the python3 -c counter."""
    # ImportError — not a SyntaxError; shouldn't accumulate in the counter
    cmd = "python3 -c \"import nonexistent_pkg_xyz_abc\""
    await collect_result(bash.run(BashArgs(command=cmd)))
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "LOOP-BREAKER" not in result.stdout or "python3 -c" not in result.stdout


@pytest.mark.asyncio
async def test_successful_python_c_does_not_accumulate(bash):
    """Successful python3 -c runs don't trigger the breaker."""
    await collect_result(bash.run(BashArgs(command="python3 -c \"print(1+1)\"")))
    result = await collect_result(bash.run(BashArgs(command="python3 -c \"print(2+2)\"")))
    assert "LOOP-BREAKER" not in result.stdout

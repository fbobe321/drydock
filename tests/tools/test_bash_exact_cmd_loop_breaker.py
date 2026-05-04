"""Regression tests for the exact-command repetition loop-breaker.

The model sometimes reruns the same bash command 5+ times (timeit benchmarks,
import-check one-liners, etc.) even though each run's output varies slightly
(different timing, different object id).  The hash-based dedup never fires
because hashes differ.  This loop-breaker fires on the 5th identical run
regardless of output content.
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
async def test_four_runs_no_breaker(bash, tmp_path):
    """Running the same command 4 times does not trigger the loop-breaker."""
    cmd = "echo hello_unique_test_marker"
    for _ in range(4):
        result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "LOOP-BREAKER" not in result.stdout


@pytest.mark.asyncio
async def test_fifth_run_triggers_loop_breaker(bash, tmp_path):
    """5th run of the same exact command triggers the loop-breaker."""
    cmd = "echo identical_cmd_loop_test"
    for _ in range(5):
        result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "LOOP-BREAKER" in result.stdout
    assert "5" in result.stdout


@pytest.mark.asyncio
async def test_write_command_exempt(bash, tmp_path):
    """Commands that write files (> redirect) are exempt from the counter."""
    outfile = tmp_path / "out.txt"
    cmd = f"echo data > {outfile}"
    for _ in range(5):
        result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "LOOP-BREAKER" not in result.stdout


@pytest.mark.asyncio
async def test_distinct_commands_no_breaker(bash, tmp_path):
    """Different commands don't share the run counter."""
    for i in range(6):
        result = await collect_result(bash.run(BashArgs(command=f"echo cmd_{i}")))
    assert "LOOP-BREAKER" not in result.stdout

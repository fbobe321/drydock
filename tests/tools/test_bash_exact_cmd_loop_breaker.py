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


@pytest.mark.asyncio
async def test_sed_i_escape_loop_triggers_breaker(bash, tmp_path):
    """sed -i with exact same command fires the loop-breaker (harness:bash:escape_loop).

    When the sed pattern is malformed (e.g. escape mismatch), sed exits 0 but
    makes no change. The model retries the same command indefinitely. The loop-
    breaker must fire because sed -i is no longer exempt from the repetition check.
    """
    target = tmp_path / "demo.py"
    target.write_text('print("n")\n')
    # Exact same sed -i command repeated 5 times (same escaping mistake each time)
    cmd = f"sed -i 's/print(\"n/print(\"\\\\n/g' {target}"
    for _ in range(5):
        result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "LOOP-BREAKER" in result.stdout

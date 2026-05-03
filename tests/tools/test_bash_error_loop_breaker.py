"""Regression test: bash loop-breaker for commands that fail with varying output.

If the same command returns non-zero exit N times in the same session,
bash must return an advisory NOTICE on the 5th+ call instead of the real
output — even if the stderr/stdout differs between calls (so the hash-based
dedup doesn't trigger).  Mirrors the admiral-logged pattern where the model
called `python3 -m tool_agent list` 14 times because each traceback had
slightly different content.
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
async def test_error_loop_breaker_triggers_on_fifth_failure(bash, tmp_path):
    # Simulate a command that fails each time but with varying stderr
    # (different counter in output) so the hash-based dedup never fires.
    # We use a counter file to make each run output unique.
    counter_file = tmp_path / "n.txt"
    counter_file.write_text("0")
    cmd = (
        f"N=$(cat {counter_file}); "
        f"echo $((N+1)) > {counter_file}; "
        f"python3 -c \"import sys; print('attempt', $((N+1)), file=sys.stderr); sys.exit(1)\""
    )

    for i in range(1, 5):
        result = await collect_result(bash.run(BashArgs(command=cmd)))
        assert result.returncode != 0
        assert "NOTICE" not in result.stdout, f"NOTICE appeared too early on call {i}"

    # 5th call triggers the loop-breaker.
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "NOTICE" in result.stdout


@pytest.mark.asyncio
async def test_error_loop_breaker_does_not_trigger_on_zero_exit(bash, tmp_path):
    # Successful commands must never trigger the error loop-breaker.
    # Use varying output to avoid the hash-based dedup too.
    counter_file = tmp_path / "c.txt"
    counter_file.write_text("0")
    cmd = (
        f"N=$(cat {counter_file}); "
        f"echo $((N+1)) > {counter_file}; "
        f"echo ok $((N+1))"
    )
    for _ in range(6):
        result = await collect_result(bash.run(BashArgs(command=cmd)))
        assert result.returncode == 0
        assert "NOTICE" not in result.stdout


@pytest.mark.asyncio
async def test_error_loop_breaker_independent_per_command(bash, tmp_path):
    # The counter must be per-command. Four failures for cmd A must not
    # trigger the breaker for cmd B.
    counter_a = tmp_path / "a.txt"
    counter_a.write_text("0")
    cmd_a = (
        f"N=$(cat {counter_a}); echo $((N+1)) > {counter_a}; "
        f"python3 -c \"import sys; print($((N+1)), file=sys.stderr); sys.exit(1)\""
    )
    counter_b = tmp_path / "b.txt"
    counter_b.write_text("0")
    cmd_b = (
        f"N=$(cat {counter_b}); echo $((N+1)) > {counter_b}; "
        f"python3 -c \"import sys; print($((N+1)), file=sys.stderr); sys.exit(2)\""
    )

    for _ in range(4):
        await collect_result(bash.run(BashArgs(command=cmd_a)))

    result = await collect_result(bash.run(BashArgs(command=cmd_b)))
    assert "NOTICE" not in result.stdout

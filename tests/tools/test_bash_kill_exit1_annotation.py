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
async def test_kill_exit1_annotates_no_such_process(bash):
    # `kill` on a non-existent PID returns exit code 1 (shell may vary).
    # The annotation must NOT say "grep/find" — it should mention "kill".
    result = await collect_result(bash.run(BashArgs(command="kill 999999999")))
    assert result.returncode in (1, 2)
    assert "kill" in result.stdout
    assert "grep" not in result.stdout.lower()
    assert "find" not in result.stdout.lower()


@pytest.mark.asyncio
async def test_kill_bang_exit1_annotates_no_such_process(bash):
    # `kill $!` when no background job is running returns exit code 1 or 2.
    result = await collect_result(bash.run(BashArgs(command="kill $!")))
    assert result.returncode in (1, 2)
    assert "kill" in result.stdout
    assert "grep" not in result.stdout.lower()


@pytest.mark.asyncio
async def test_grep_exit1_keeps_original_annotation(bash, tmp_path):
    # Plain grep with no matches still gets the grep/find annotation.
    (tmp_path / "empty.py").write_text("x = 1\n")
    result = await collect_result(bash.run(BashArgs(
        command=f"grep 'NONEXISTENT_SYMBOL_XYZ' {tmp_path}/empty.py"
    )))
    assert result.returncode == 1
    assert "grep" in result.stdout.lower() or "find" in result.stdout.lower()

from __future__ import annotations

import pytest

from tests.mock.utils import collect_result
from drydock.core.tools.base import BaseToolState, ToolError, ToolPermission
from drydock.core.tools.builtins.bash import Bash, BashArgs, BashToolConfig


@pytest.fixture
def bash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = BashToolConfig()
    return Bash(config=config, state=BaseToolState())


@pytest.mark.asyncio
async def test_runs_echo_successfully(bash):
    result = await collect_result(bash.run(BashArgs(command="echo hello")))

    assert result.returncode == 0
    assert result.stdout == "hello\n"
    assert result.stderr == ""


@pytest.mark.asyncio
async def test_nonzero_exit_returns_result_not_error(bash):
    # Non-zero exit must return BashResult (advisory), not raise ToolError.
    # Raising blocks the model: it sees <tool_error> with no useful output
    # and retries identically — the exact pattern that causes grep retry storms.
    from drydock.core.tools.builtins.bash import BashResult
    result = await collect_result(bash.run(BashArgs(command="cat missing_file.txt")))
    assert isinstance(result, BashResult)
    assert result.returncode == 1
    assert "No such file or directory" in result.stderr or "No such file" in result.stdout
    assert "[Exit code 1]" in result.stdout


@pytest.mark.asyncio
async def test_grep_no_matches_returns_advisory_result(bash, tmp_path):
    # grep exits 1 when no matches — that's NOT a failure, it's information.
    # The model must see this as a normal result with a hint, not a ToolError.
    from drydock.core.tools.builtins.bash import BashResult
    result = await collect_result(
        bash.run(BashArgs(command="grep -rn 'THIS_DOES_NOT_EXIST' ."))
    )
    assert isinstance(result, BashResult)
    assert result.returncode == 1
    assert "no matches found" in result.stdout.lower()


@pytest.mark.asyncio
async def test_uses_effective_workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = BashToolConfig()
    bash_tool = Bash(config=config, state=BaseToolState())

    result = await collect_result(bash_tool.run(BashArgs(command="pwd")))

    assert result.stdout.strip() == str(tmp_path)


@pytest.mark.asyncio
async def test_handles_timeout(bash):
    # Timeout returns advisory BashResult (not ToolError) so the model
    # can learn from it rather than blindly retrying the same command.
    from drydock.core.tools.builtins.bash import BashResult
    result = await collect_result(bash.run(BashArgs(command="sleep 2", timeout=1)))
    assert isinstance(result, BashResult)
    assert "timed out after 1s" in result.stdout
    assert result.returncode == 124


@pytest.mark.asyncio
async def test_timeout_escalates_on_repeat(bash):
    """Second+ timeout on same command includes escalated stop-retrying hint."""
    from drydock.core.tools.builtins.bash import BashResult
    r1 = await collect_result(bash.run(BashArgs(command="sleep 2", timeout=1)))
    assert isinstance(r1, BashResult)
    assert "TIMEOUT #" not in r1.stdout
    assert "background" in r1.stdout.lower()
    r2 = await collect_result(bash.run(BashArgs(command="sleep 2", timeout=1)))
    assert isinstance(r2, BashResult)
    assert "TIMEOUT #2" in r2.stdout
    assert "STOP" in r2.stdout


@pytest.mark.asyncio
async def test_truncates_output_to_max_bytes(bash):
    config = BashToolConfig(max_output_bytes=5)
    bash_tool = Bash(config=config, state=BaseToolState())

    result = await collect_result(
        bash_tool.run(BashArgs(command="printf 'abcdefghij'"))
    )

    assert result.stdout == "abcde"
    assert result.stderr == ""
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_decodes_non_utf8_bytes(bash):
    result = await collect_result(bash.run(BashArgs(command="printf '\\xff\\xfe'")))

    # accept both possible encodings, as some shells emit escaped bytes as literal strings
    assert result.stdout in {"��", "\xff\xfe", r"\xff\xfe"}
    assert result.stderr == ""


def test_resolve_permission():
    config = BashToolConfig(allowlist=["echo", "pwd"], denylist=["rm"])
    bash_tool = Bash(config=config, state=BaseToolState())

    allowlisted = bash_tool.resolve_permission(BashArgs(command="echo hi"))
    denylisted = bash_tool.resolve_permission(BashArgs(command="rm -rf /tmp"))
    mixed = bash_tool.resolve_permission(BashArgs(command="pwd && whoami"))
    empty = bash_tool.resolve_permission(BashArgs(command=""))

    assert allowlisted is ToolPermission.ALWAYS
    assert denylisted is ToolPermission.NEVER
    assert mixed is None
    assert empty is None

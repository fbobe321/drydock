"""Regression test: bash tool proactive heredoc-write confirmation.

Stress run on 2026-05-03 showed 311 admiral `harness:bash:heredoc_loop`
fires (dispatch queue).  Pattern: model writes a plugin file via bash
heredoc (`cat <<EOF > file.py`), gets empty stdout (rc=0), doesn't know
the file was created, and re-runs the same heredoc.  The old dedup check
only fired a hint on the 3rd identical run; by then the model had already
looped twice.

Fix: detect heredoc-write on the FIRST call.  If rc=0 and stdout is empty
and the target file now exists on disk, inject a "File written: path
(N lines, N bytes)" confirmation immediately.  The model sees the file
landed, moves on, and never retries.
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
async def test_heredoc_write_confirmation_first_call(bash, tmp_path):
    """First heredoc write to a new file gets 'File written' confirmation."""
    target = tmp_path / "plugin.py"
    cmd = f"cat <<EOF > {target}\nprint('hello')\nEOF"
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "File written" in result.stdout
    assert str(target) in result.stdout or target.name in result.stdout
    assert "lines" in result.stdout
    assert "bytes" in result.stdout


@pytest.mark.asyncio
async def test_heredoc_write_confirmation_quoted_eof(bash, tmp_path):
    """Quoted 'EOF' marker also gets confirmation."""
    target = tmp_path / "config.py"
    cmd = f"cat << 'EOF' > {target}\nx = 1\nEOF"
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "File written" in result.stdout


@pytest.mark.asyncio
async def test_heredoc_write_confirmation_append(bash, tmp_path):
    """Append redirect (>>) also triggers confirmation when file exists."""
    target = tmp_path / "data.txt"
    target.write_text("line1\n")
    cmd = f"cat <<EOF >> {target}\nline2\nEOF"
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "File written" in result.stdout or "lines" in result.stdout


@pytest.mark.asyncio
async def test_heredoc_write_no_confirmation_when_file_missing(bash, tmp_path):
    """If file doesn't exist after write (e.g. permission error), no false positive."""
    # A path in a non-existent dir — the cat will fail with rc != 0
    cmd = f"cat <<EOF > /nonexistent_dir_xyz/plugin.py\nprint('x')\nEOF"
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "File written" not in result.stdout


@pytest.mark.asyncio
async def test_non_heredoc_command_unaffected(bash):
    """Regular commands that produce empty stdout are not mis-labeled."""
    cmd = "true"
    result = await collect_result(bash.run(BashArgs(command=cmd)))
    assert "File written" not in result.stdout


@pytest.mark.asyncio
async def test_heredoc_loop_breaker_non_eof_delimiter(bash, tmp_path):
    """Loop-breaker targets heredoc message for non-EOF delimiters (CONTENT, PYTHON, etc).

    Before fix: regex only matched 'EOF'; non-EOF delimiters fell through to the
    generic 'EDIT SOURCE CODE' message, which confused the model.
    After fix: any alpha delimiter triggers the targeted heredoc hint.
    """
    target = tmp_path / "plugin.py"
    cmd = f"cat <<CONTENT > {target}\nprint('hello')\nCONTENT"

    # Run 3 times to trigger the hash-based loop breaker
    for _ in range(3):
        result = await collect_result(bash.run(BashArgs(command=cmd)))

    # The 3rd run must produce the heredoc-targeted notice, not the generic one
    assert "cat command" in result.stdout or "re-run this cat" in result.stdout, (
        "Expected heredoc-targeted loop notice, got: " + result.stdout[:200]
    )
    assert "EDIT SOURCE CODE" not in result.stdout, (
        "Got generic loop notice instead of heredoc-targeted one"
    )

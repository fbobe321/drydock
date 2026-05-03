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
async def test_binary_grep_match_adds_hint(bash, tmp_path):
    # Create a fake binary file that grep will skip and warn about.
    bin_file = tmp_path / "module.pyc"
    bin_file.write_bytes(b"\x00\x01\x02BaseStoragePlugin\x03\x04")

    result = await collect_result(bash.run(BashArgs(
        command=f"grep -r 'BaseStoragePlugin' {tmp_path}"
    )))

    assert result.returncode == 0
    assert "binary file" in result.stderr
    assert "--include='*.py'" in result.stderr, (
        "hint to add --include='*.py' should appear when grep emits binary-file warning"
    )


@pytest.mark.asyncio
async def test_binary_grep_hint_not_added_on_clean_stderr(bash, tmp_path):
    # Normal grep with no binary files should not inject the hint.
    py_file = tmp_path / "module.py"
    py_file.write_text("class BaseStoragePlugin: pass\n")

    result = await collect_result(bash.run(BashArgs(
        command=f"grep -r 'BaseStoragePlugin' {tmp_path}"
    )))

    assert result.returncode == 0
    assert "--include='*.py'" not in result.stderr

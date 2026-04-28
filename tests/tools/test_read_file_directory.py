"""Regression test: read_file on a directory path includes a listing.

Admiral logs showed retry_after_error:read_file when the model passes
a directory path (e.g. "tool_agent/" instead of "tool_agent/__init__.py").
The old error was opaque ("Path is a directory, not a file") with no
listing, so the model retried identically. Fix: include directory contents
in the error so the model can pick the right file on its next call.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from drydock.core.tools.base import BaseToolState, ToolError
from drydock.core.tools.builtins.read_file import ReadFile, ReadFileArgs, ReadFileToolConfig


async def _run_and_collect(tool, args, ctx):
    """Drain the async generator, re-raising any ToolError."""
    results = []
    async for event in tool.run(args, ctx):
        results.append(event)
    return results


@pytest.mark.asyncio
async def test_read_directory_includes_listing(tmp_path):
    """read_file on a directory path should include a listing of directory contents."""
    pkg_dir = tmp_path / "mypkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("# init")
    (pkg_dir / "cli.py").write_text("# cli")
    (pkg_dir / "utils.py").write_text("# utils")

    tool = ReadFile(ReadFileToolConfig(), BaseToolState())
    args = ReadFileArgs(path=str(pkg_dir))
    ctx = MagicMock()
    ctx.read_file_state = {}

    with pytest.raises(ToolError) as exc_info:
        await _run_and_collect(tool, args, ctx)

    error_msg = str(exc_info.value)
    assert "is a directory" in error_msg
    assert "__init__.py" in error_msg, "Directory listing should include __init__.py"
    assert "cli.py" in error_msg, "Directory listing should include cli.py"
    assert "utils.py" in error_msg, "Directory listing should include utils.py"


@pytest.mark.asyncio
async def test_read_directory_empty_no_crash(tmp_path):
    """read_file on an empty directory should still return a useful error."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    tool = ReadFile(ReadFileToolConfig(), BaseToolState())
    args = ReadFileArgs(path=str(empty_dir))
    ctx = MagicMock()
    ctx.read_file_state = {}

    with pytest.raises(ToolError) as exc_info:
        await _run_and_collect(tool, args, ctx)

    assert "is a directory" in str(exc_info.value)

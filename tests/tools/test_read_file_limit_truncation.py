"""Regression test: read_file with limit= sets was_truncated and appends pagination hint.

The model (Gemma 4) called read_file with limit=100 on large files, received
was_truncated=False and no hint, and then re-read the same file 10+ times
confused about why it was seeing partial content. Fix: set was_truncated=True
when the line limit stops reading, and append an "offset=N to read more" hint
so the model knows to paginate rather than re-read.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.read_file import ReadFile, ReadFileArgs, ReadFileResult, ReadFileToolConfig


async def _run_result(tool, args, ctx) -> ReadFileResult:
    async for event in tool.run(args, ctx):
        if isinstance(event, ReadFileResult):
            return event
    raise AssertionError("no ReadFileResult yielded")


@pytest.mark.asyncio
async def test_limit_sets_was_truncated_and_adds_hint(tmp_path):
    """was_truncated=True and pagination hint appended when limit stops the read."""
    f = tmp_path / "big.py"
    f.write_text("\n".join(f"line{i}" for i in range(1, 201)))  # 200 lines

    tool = ReadFile(ReadFileToolConfig(), BaseToolState())
    args = ReadFileArgs(path=str(f), offset=0, limit=100)
    ctx = MagicMock()
    ctx.read_file_state = {}

    result = await _run_result(tool, args, ctx)

    assert result.was_truncated is True
    assert result.lines_read == 100
    assert "offset=100" in result.content
    assert "read more" in result.content


@pytest.mark.asyncio
async def test_no_truncation_when_file_fits_in_limit(tmp_path):
    """was_truncated=False when file has fewer lines than the limit."""
    f = tmp_path / "small.py"
    f.write_text("\n".join(f"line{i}" for i in range(1, 11)))  # 10 lines

    tool = ReadFile(ReadFileToolConfig(), BaseToolState())
    args = ReadFileArgs(path=str(f), offset=0, limit=100)
    ctx = MagicMock()
    ctx.read_file_state = {}

    result = await _run_result(tool, args, ctx)

    assert result.was_truncated is False
    assert "offset=" not in result.content


@pytest.mark.asyncio
async def test_offset_pagination_hint_uses_correct_next_offset(tmp_path):
    """Hint shows the correct next offset when reading a middle chunk."""
    f = tmp_path / "file.py"
    f.write_text("\n".join(f"line{i}" for i in range(1, 301)))  # 300 lines

    tool = ReadFile(ReadFileToolConfig(), BaseToolState())
    args = ReadFileArgs(path=str(f), offset=50, limit=100)
    ctx = MagicMock()
    ctx.read_file_state = {}

    result = await _run_result(tool, args, ctx)

    assert result.was_truncated is True
    assert "offset=150" in result.content  # 50 + 100

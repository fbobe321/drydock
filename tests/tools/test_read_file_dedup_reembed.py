"""Regression test: read_file dedup re-embeds cached content instead of pointing
at a possibly-truncated prior tool_result.

The model (Gemma 4) looped re-reading cli.py with limit=100 because:
1. First read: stored in read_state, returned truncated content.
2. _truncate_old_tool_results pruned that tool_result from message history.
3. Second read (same offset/limit/mtime): dedup fired with "use earlier result."
4. Model couldn't find the earlier result (truncated) and re-read again → loop.

Fix: when dedup fires, embed the cached content directly in the response
rather than pointing at the prior (possibly absent) tool_result.
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
async def test_dedup_reembeds_cached_content(tmp_path):
    """Second identical read returns cached content, not a pointer stub."""
    f = tmp_path / "cli.py"
    f.write_text("def main():\n    pass\n")

    tool = ReadFile(ReadFileToolConfig(), BaseToolState())
    read_state: dict = {}
    ctx = MagicMock()
    ctx.read_file_state = read_state

    args = ReadFileArgs(path=str(f), offset=0, limit=100)

    # First read populates the cache.
    result1 = await _run_result(tool, args, ctx)
    assert "def main" in result1.content

    # Second read with same args/mtime must return real content, not a pointer.
    result2 = await _run_result(tool, args, ctx)
    assert "def main" in result2.content, "dedup must re-embed content, not point at prior result"
    assert "re-embedded" in result2.content


@pytest.mark.asyncio
async def test_dedup_does_not_fire_after_file_changes(tmp_path):
    """Dedup must not fire if the file has been modified on disk."""
    f = tmp_path / "cli.py"
    f.write_text("version = 1\n")

    tool = ReadFile(ReadFileToolConfig(), BaseToolState())
    read_state: dict = {}
    ctx = MagicMock()
    ctx.read_file_state = read_state

    args = ReadFileArgs(path=str(f), offset=0, limit=100)

    result1 = await _run_result(tool, args, ctx)
    assert "version = 1" in result1.content

    # Modify the file (mtime changes).
    import time; time.sleep(0.01)
    f.write_text("version = 2\n")

    result2 = await _run_result(tool, args, ctx)
    assert "version = 2" in result2.content, "should read updated file, not cached content"
    assert "re-embedded" not in result2.content


@pytest.mark.asyncio
async def test_dedup_escalates_on_repeated_reads(tmp_path):
    """Third and later identical reads get an escalating REPEATED READ advisory."""
    f = tmp_path / "cli.py"
    f.write_text("def main():\n    pass\n")

    tool = ReadFile(ReadFileToolConfig(), BaseToolState())
    read_state: dict = {}
    ctx = MagicMock()
    ctx.read_file_state = read_state

    args = ReadFileArgs(path=str(f), offset=0, limit=100)

    # First read: no dedup.
    r1 = await _run_result(tool, args, ctx)
    assert "re-embedded" not in r1.content
    assert "REPEATED READ" not in r1.content

    # Second read: first dedup hit — gentle re-embed message.
    r2 = await _run_result(tool, args, ctx)
    assert "re-embedded" in r2.content
    assert "REPEATED READ" not in r2.content
    assert read_state[str(f)]["dedup_count"] == 1

    # Third read: second dedup hit — escalated advisory.
    r3 = await _run_result(tool, args, ctx)
    assert "REPEATED READ #2" in r3.content
    assert "def main" in r3.content
    assert read_state[str(f)]["dedup_count"] == 2

    # Fourth read: third dedup hit — counter increments.
    r4 = await _run_result(tool, args, ctx)
    assert "REPEATED READ #3" in r4.content
    assert read_state[str(f)]["dedup_count"] == 3


@pytest.mark.asyncio
async def test_dedup_stores_lines_read_and_was_truncated(tmp_path):
    """read_state must record lines_read and was_truncated for correct dedup re-embedding."""
    f = tmp_path / "big.py"
    f.write_text("\n".join(f"line{i}" for i in range(1, 201)))  # 200 lines

    tool = ReadFile(ReadFileToolConfig(), BaseToolState())
    read_state: dict = {}
    ctx = MagicMock()
    ctx.read_file_state = read_state

    args = ReadFileArgs(path=str(f), offset=0, limit=50)
    await _run_result(tool, args, ctx)

    path_key = str(f)
    assert "lines_read" in read_state[path_key]
    assert read_state[path_key]["lines_read"] == 50
    assert read_state[path_key]["was_truncated"] is True

    # Second read: dedup fires, re-embeds with correct metadata.
    result2 = await _run_result(tool, args, ctx)
    assert result2.lines_read == 50
    assert result2.was_truncated is True

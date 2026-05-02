"""Regression test: read_file dedup fires correctly when model alternates offsets.

Bug: the single-slot cache (read_state[path_key]) was overwritten on every new
(offset, limit) pair.  Reading at offset=0 stored slot A; reading at offset=50
(empty, past EOF) overwrote slot A with slot B.  Re-reading at offset=0 then
saw no cache hit because the slot had offset=50, triggering a fresh disk read
and leaving the model in an infinite alternating-offset loop.

Fix: track each (offset, limit) combination independently in _slots so all
combinations are deduped regardless of read order.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.read_file import (
    ReadFile,
    ReadFileArgs,
    ReadFileResult,
    ReadFileToolConfig,
)


async def _run(tool, args, ctx) -> ReadFileResult:
    async for event in tool.run(args, ctx):
        if isinstance(event, ReadFileResult):
            return event
    raise AssertionError("no ReadFileResult yielded")


@pytest.mark.asyncio
async def test_alternating_offsets_both_deduped(tmp_path):
    """offset=0 dedup survives an interleaved offset=50 read on a short file."""
    f = tmp_path / "short.py"
    f.write_text("line1\nline2\nline3\n")  # only 3 lines

    tool = ReadFile(ReadFileToolConfig(), BaseToolState())
    read_state: dict = {}
    ctx = MagicMock()
    ctx.read_file_state = read_state

    args0 = ReadFileArgs(path=str(f), offset=0)
    args50 = ReadFileArgs(path=str(f), offset=50)

    # First read at offset=0 — populates cache for (0, None).
    r1 = await _run(tool, args0, ctx)
    assert "line1" in r1.content

    # Read at offset=50 — past EOF, returns empty (or minimal) content.
    r2 = await _run(tool, args50, ctx)
    # Content may be empty; just ensure it doesn't crash.

    # Re-read at offset=0 — MUST dedup and re-embed, NOT do a fresh disk read.
    r3 = await _run(tool, args0, ctx)
    assert "line1" in r3.content, "offset=0 dedup must survive interleaved offset=50 read"
    assert "re-embedded" in r3.content or "REPEATED" in r3.content


@pytest.mark.asyncio
async def test_each_slot_deduped_independently(tmp_path):
    """Each (offset, limit) slot fires dedup independently."""
    f = tmp_path / "multi.py"
    f.write_text("\n".join(f"line{i}" for i in range(200)) + "\n")

    tool = ReadFile(ReadFileToolConfig(), BaseToolState())
    read_state: dict = {}
    ctx = MagicMock()
    ctx.read_file_state = read_state

    args_a = ReadFileArgs(path=str(f), offset=0, limit=50)
    args_b = ReadFileArgs(path=str(f), offset=50, limit=50)

    # First reads populate two independent slots.
    r_a1 = await _run(tool, args_a, ctx)
    r_b1 = await _run(tool, args_b, ctx)
    assert "line0" in r_a1.content
    assert "line50" in r_b1.content

    # Re-read slot A after slot B — must dedup.
    r_a2 = await _run(tool, args_a, ctx)
    assert "line0" in r_a2.content
    assert "re-embedded" in r_a2.content or "REPEATED" in r_a2.content

    # Re-read slot B — must also dedup.
    r_b2 = await _run(tool, args_b, ctx)
    assert "line50" in r_b2.content
    assert "re-embedded" in r_b2.content or "REPEATED" in r_b2.content


@pytest.mark.asyncio
async def test_write_file_compat_after_read(tmp_path):
    """read_state[path_key] remains non-None after reads, so write_file can proceed."""
    f = tmp_path / "code.py"
    f.write_text("x = 1\n")

    tool = ReadFile(ReadFileToolConfig(), BaseToolState())
    read_state: dict = {}
    ctx = MagicMock()
    ctx.read_file_state = read_state

    args = ReadFileArgs(path=str(f), offset=0)
    await _run(tool, args, ctx)

    # write_file checks: read_state.get(path_key) is not None
    path_key = str(f)
    assert read_state.get(path_key) is not None, "top-level entry must exist for write_file compat"
    assert read_state[path_key].get("timestamp") is not None

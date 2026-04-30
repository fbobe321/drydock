"""Regression test: search_replace must not write conflict markers to files.

Observed in stress run 2026-04-30: when the model sends a truncated
SEARCH/REPLACE block (e.g. missing >>>>>>> REPLACE closer), the regex fails
to parse it, and the NO_BLOCKS fallback wrote the raw content — including
'<<<<<<< SEARCH' markers — directly to the file. On the next turn the model
found markers in the file and entered a retry loop trying to find original
code that no longer existed.

Fix: in NO_BLOCKS path, detect SEARCH/REPLACE marker characters in raw_content
and return an error instead of writing the content to disk.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from drydock.core.tools.base import BaseToolState, InvokeContext
from drydock.core.tools.builtins.search_replace import (
    SearchReplace,
    SearchReplaceArgs,
    SearchReplaceConfig,
    SearchReplaceResult,
)


@pytest.fixture
def tool():
    return SearchReplace(SearchReplaceConfig(), BaseToolState())


@pytest.fixture
def ctx() -> InvokeContext:
    return InvokeContext(tool_call_id="tc_1", read_file_state={})


async def _call(tool, args: SearchReplaceArgs, ctx: InvokeContext) -> SearchReplaceResult:
    results = []
    async for r in tool.run(args, ctx):
        if isinstance(r, SearchReplaceResult):
            results.append(r)
    assert results, "Tool produced no output"
    return results[-1]


@pytest.mark.asyncio
async def test_truncated_block_no_replace_closer(tmp_path: Path, tool, ctx):
    """Malformed block missing >>>>>>> REPLACE must NOT be written to disk."""
    target = tmp_path / "cli.py"
    target.write_text("def main():\n    print('hello')\n")

    # Simulate: model sends SEARCH/REPLACE without the closing >>>>>>> REPLACE
    malformed = (
        "<<<<<<< SEARCH\n"
        "def main():\n"
        "    print('hello')\n"
        "=======\n"
        "def main():\n"
        "    print('world')\n"
    )

    ctx.read_file_state[str(target)] = {
        "content": target.read_text(),
        "timestamp": target.stat().st_mtime_ns,
        "offset": 0,
        "limit": None,
    }
    result = await _call(
        tool,
        SearchReplaceArgs(file_path=str(target), content=malformed),
        ctx,
    )

    # Must not write anything — the malformed block is rejected
    assert result.blocks_applied == 0
    # The file must be unchanged regardless of the error path taken
    assert target.read_text() == "def main():\n    print('hello')\n"


@pytest.mark.asyncio
async def test_content_with_only_open_marker(tmp_path: Path, tool, ctx):
    """Content containing only '<<<<<<< SEARCH' (no equals/close) must not write."""
    target = tmp_path / "server.py"
    target.write_text("class Server:\n    pass\n")

    ctx.read_file_state[str(target)] = {
        "content": target.read_text(),
        "timestamp": target.stat().st_mtime_ns,
        "offset": 0,
        "limit": None,
    }
    result = await _call(
        tool,
        SearchReplaceArgs(file_path=str(target), content="<<<<<<< SEARCH\nclass Server:\n    pass\n"),
        ctx,
    )

    assert result.blocks_applied == 0
    assert target.read_text() == "class Server:\n    pass\n"


@pytest.mark.asyncio
async def test_valid_block_still_works(tmp_path: Path, tool, ctx):
    """A well-formed SEARCH/REPLACE block must still apply correctly."""
    target = tmp_path / "app.py"
    target.write_text("def greet():\n    return 'hello'\n")

    ctx.read_file_state[str(target)] = {
        "content": target.read_text(),
        "timestamp": target.stat().st_mtime_ns,
        "offset": 0,
        "limit": None,
    }
    valid_block = (
        "<<<<<<< SEARCH\n"
        "def greet():\n"
        "    return 'hello'\n"
        "=======\n"
        "def greet():\n"
        "    return 'world'\n"
        ">>>>>>> REPLACE\n"
    )
    result = await _call(
        tool,
        SearchReplaceArgs(file_path=str(target), content=valid_block),
        ctx,
    )

    assert result.blocks_applied == 1
    assert "world" in target.read_text()

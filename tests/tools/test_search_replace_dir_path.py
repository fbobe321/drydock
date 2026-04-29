"""Regression test: search_replace returns actionable result when passed a directory.

Admiral logs (2026-04-28) showed 38 instances of retry_after_error:search_replace
with error head 'file: /data3/.../tool_agent' — the model was passing the package
directory path instead of a specific file path. The old code raised ToolError
("Path is not a file") which the framework turned into a tool error, and the model
retried the same bad path in a loop.

Fix: catch the directory-path case in run() and yield a SearchReplaceResult listing
the files in the directory. The model can then correct the path instead of looping.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from drydock.core.tools.base import BaseToolState, InvokeContext
from drydock.core.tools.builtins.search_replace import (
    SearchReplace,
    SearchReplaceArgs,
    SearchReplaceConfig,
    SearchReplaceResult,
)


async def _collect(gen):
    results = []
    async for item in gen:
        results.append(item)
    return results


@pytest.fixture
def tool():
    return SearchReplace(SearchReplaceConfig(), BaseToolState())


@pytest.fixture
def ctx() -> InvokeContext:
    return InvokeContext(tool_call_id="tc_1", read_file_state={})


@pytest.mark.asyncio
async def test_directory_path_returns_result_not_error(tool, ctx, tmp_path):
    """Passing a directory path should yield a SearchReplaceResult with the file list."""
    # Create some files in the temp dir
    (tmp_path / "cli.py").write_text("print('cli')\n")
    (tmp_path / "tools.py").write_text("def run(): pass\n")

    args = SearchReplaceArgs(
        file_path=str(tmp_path),
        content="<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE",
    )
    results = await _collect(tool.run(args, ctx))
    assert results, "expected at least one result"
    result = results[-1]
    assert isinstance(result, SearchReplaceResult)
    assert result.blocks_applied == 0
    assert "PATH ERROR" in result.content
    assert "cli.py" in result.content
    assert "tools.py" in result.content


@pytest.mark.asyncio
async def test_directory_path_does_not_raise(tool, ctx, tmp_path):
    """Verify no exception propagates when a directory path is given."""
    args = SearchReplaceArgs(
        file_path=str(tmp_path),
        content="<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE",
    )
    # Should not raise
    results = await _collect(tool.run(args, ctx))
    assert any(isinstance(r, SearchReplaceResult) for r in results)


@pytest.mark.asyncio
async def test_directory_path_repeated_call_still_returns_result(tool, ctx, tmp_path):
    """Second call with the same directory path still returns a result (not an error)."""
    (tmp_path / "main.py").write_text("x = 1\n")
    args = SearchReplaceArgs(
        file_path=str(tmp_path),
        content="<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE",
    )
    for _ in range(3):
        results = await _collect(tool.run(args, ctx))
        result = results[-1]
        assert isinstance(result, SearchReplaceResult)
        assert result.blocks_applied == 0
        assert "PATH ERROR" in result.content


@pytest.mark.asyncio
async def test_directory_path_escalates_on_second_call(tool, ctx, tmp_path):
    """Second call with the same directory path includes REPEATED ERROR escalation."""
    (tmp_path / "cli.py").write_text("x = 1\n")
    args = SearchReplaceArgs(
        file_path=str(tmp_path),
        content="<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE",
    )
    # First call — no escalation
    results1 = await _collect(tool.run(args, ctx))
    assert "REPEATED ERROR" not in results1[-1].content

    # Second call — escalation fires
    results2 = await _collect(tool.run(args, ctx))
    assert "REPEATED ERROR" in results2[-1].content
    assert "#2" in results2[-1].content

    # Third call — escalation still present with updated count
    results3 = await _collect(tool.run(args, ctx))
    assert "REPEATED ERROR" in results3[-1].content
    assert "#3" in results3[-1].content

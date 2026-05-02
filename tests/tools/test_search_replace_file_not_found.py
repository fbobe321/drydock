"""Regression test: search_replace returns advisory result when file does not exist.

Admiral logs (2026-05-02) showed 18 instances of retry_after_error:search_replace
with error head '<tool_error>search_replace failed: File does not exist: /dat...' —
the model was trying to edit a path that didn't exist and retrying it 18+ times
because the ToolError gave no actionable guidance (no directory listing, no suggestion
to use write_file).

Fix: catch the "File does not exist" ToolError in run() and yield a SearchReplaceResult
with the parent directory listing and a write_file suggestion. On 2nd+ retry for the
same path, include a project-wide .py file listing.
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


def _make_tool(tmp_path, cwd=None):
    state = BaseToolState()
    config = SearchReplaceConfig()
    tool = SearchReplace(state=state, config=config)
    return tool, state


@pytest.mark.asyncio
async def test_file_not_found_returns_advisory_not_tool_error(tmp_path):
    """search_replace on a nonexistent file yields a result, not raises."""
    os.chdir(tmp_path)
    tool, _ = _make_tool(tmp_path)
    args = SearchReplaceArgs(
        file_path="nonexistent_module.py",
        content="<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE",
    )
    results = await _collect(tool.run(args))
    assert results, "expected at least one result"
    result = results[-1]
    assert isinstance(result, SearchReplaceResult)
    assert result.blocks_applied == 0
    assert "FILE NOT FOUND" in result.content or "does not exist" in result.content.lower()


@pytest.mark.asyncio
async def test_file_not_found_includes_directory_listing(tmp_path):
    """Advisory result includes sibling file names when parent dir exists."""
    os.chdir(tmp_path)
    (tmp_path / "existing.py").write_text("# existing")
    (tmp_path / "other.py").write_text("# other")
    tool, _ = _make_tool(tmp_path)
    args = SearchReplaceArgs(
        file_path="missing.py",
        content="<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE",
    )
    results = await _collect(tool.run(args))
    result = results[-1]
    assert isinstance(result, SearchReplaceResult)
    assert "existing.py" in result.content or "other.py" in result.content


@pytest.mark.asyncio
async def test_file_not_found_escalates_on_second_call(tmp_path):
    """Second call for the same missing file includes project-wide listing hint."""
    os.chdir(tmp_path)
    (tmp_path / "real.py").write_text("# real")
    tool, _ = _make_tool(tmp_path)
    args = SearchReplaceArgs(
        file_path="ghost.py",
        content="<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE",
    )
    # First call
    await _collect(tool.run(args))
    # Second call with same missing path
    results = await _collect(tool.run(args))
    result = results[-1]
    assert isinstance(result, SearchReplaceResult)
    assert "REPEATED ERROR" in result.content
    assert "write_file" in result.content


@pytest.mark.asyncio
async def test_file_not_found_suggests_write_file(tmp_path):
    """Advisory result always suggests write_file as the creation alternative."""
    os.chdir(tmp_path)
    tool, _ = _make_tool(tmp_path)
    args = SearchReplaceArgs(
        file_path="new_module.py",
        content="<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE",
    )
    results = await _collect(tool.run(args))
    result = results[-1]
    assert isinstance(result, SearchReplaceResult)
    assert "write_file" in result.content

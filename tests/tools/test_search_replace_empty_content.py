"""Regression test: search_replace empty-content raises ToolError → retry loop.

Stress run 2026-04-28 showed Gemma 4 calling search_replace with empty
content (content="") 20+ times consecutively. The ToolError path caused
panic-retry loops (feedback: never raise ToolError for loop detection).

Fix: detect empty content/file_path in the ToolError handler in run()
and yield a soft SearchReplaceResult instead of re-raising. Track count
per call-target so the 2nd+ offense escalates with project file listing.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from drydock.core.tools.base import BaseToolState, InvokeContext
from drydock.core.tools.builtins.search_replace import (
    SearchReplace,
    SearchReplaceArgs,
    SearchReplaceConfig,
    SearchReplaceResult,
)


def _make_tool() -> SearchReplace:
    config = SearchReplaceConfig()
    state = BaseToolState()
    return SearchReplace(config=config, state=state)


async def _run(tool: SearchReplace, args: SearchReplaceArgs, tmp_path: Path) -> SearchReplaceResult:
    ctx = InvokeContext(tool_call_id="test-1", read_file_state={})
    results = []
    async for item in tool.run(args, ctx):
        if isinstance(item, SearchReplaceResult):
            results.append(item)
    assert results, "Expected at least one SearchReplaceResult"
    return results[-1]


@pytest.mark.asyncio
async def test_empty_content_returns_result_not_raises(tmp_path: Path) -> None:
    """Empty content must yield a result, never raise ToolError."""
    tool = _make_tool()
    result = await _run(
        tool,
        SearchReplaceArgs(file_path="foo.py", content=""),
        tmp_path,
    )
    assert isinstance(result, SearchReplaceResult)
    assert result.blocks_applied == 0
    assert "Empty content" in result.content


@pytest.mark.asyncio
async def test_empty_content_escalates_on_repeat(tmp_path: Path) -> None:
    """2nd consecutive empty-content call adds project file listing."""
    tool = _make_tool()
    # First call
    result1 = await _run(
        tool,
        SearchReplaceArgs(file_path="foo.py", content=""),
        tmp_path,
    )
    assert "Empty content" in result1.content
    assert "#2" not in result1.content  # no escalation yet

    # Second call — same key, should escalate
    result2 = await _run(
        tool,
        SearchReplaceArgs(file_path="foo.py", content=""),
        tmp_path,
    )
    assert "#2" in result2.content
    assert "Stop retrying" in result2.content


@pytest.mark.asyncio
async def test_missing_file_path_returns_result_not_raises(tmp_path: Path) -> None:
    """Missing file_path (can't infer) must also yield a result, not raise."""
    tool = _make_tool()
    result = await _run(
        tool,
        SearchReplaceArgs(file_path="", content=""),
        tmp_path,
    )
    assert isinstance(result, SearchReplaceResult)
    assert result.blocks_applied == 0

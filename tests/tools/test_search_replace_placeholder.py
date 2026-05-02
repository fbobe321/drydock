"""Regression: search_replace returns advisory (not ToolError) for placeholder replacements.

Admiral logs showed 4 instances of retry_after_error:search_replace with
'<tool_error>search_replace failed: Your replacement contains' — model retried
the same placeholder block because the ToolError gave no actionable guidance.

Fix: yield SearchReplaceResult with a REFUSED message instead of raising ToolError.
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


async def _collect(gen):
    results = []
    async for r in gen:
        results.append(r)
    return results


def _make_tool(tmp_path):
    state = BaseToolState()
    ctx = InvokeContext(session_id="test", cwd=str(tmp_path))
    return SearchReplace(config=SearchReplaceConfig(), state=state, invoke_context=ctx)


@pytest.mark.asyncio
async def test_placeholder_returns_advisory_not_tool_error(tmp_path):
    """'# rest of code' replacement yields a SearchReplaceResult, not a ToolError."""
    target = tmp_path / "foo.py"
    target.write_text("def foo():\n    return 1\n")

    tool = _make_tool(tmp_path)
    args = SearchReplaceArgs(
        file_path=str(target),
        content=(
            "<<<<<<< SEARCH\ndef foo():\n    return 1\n=======\ndef foo():\n"
            "    # rest of code\n>>>>>>> REPLACE\n"
        ),
    )
    results = await _collect(tool.run(args))
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, SearchReplaceResult)
    assert r.blocks_applied == 0
    assert "REFUSED" in r.content
    assert "placeholder" in r.content.lower()
    # File must be unchanged
    assert target.read_text() == "def foo():\n    return 1\n"


@pytest.mark.asyncio
async def test_ellipsis_placeholder_returns_advisory(tmp_path):
    """Bare '...' as replacement also yields advisory."""
    target = tmp_path / "bar.py"
    target.write_text("x = 1\n")

    tool = _make_tool(tmp_path)
    args = SearchReplaceArgs(
        file_path=str(target),
        content="<<<<<<< SEARCH\nx = 1\n=======\n...\n>>>>>>> REPLACE\n",
    )
    results = await _collect(tool.run(args))
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, SearchReplaceResult)
    assert r.blocks_applied == 0
    assert target.read_text() == "x = 1\n"

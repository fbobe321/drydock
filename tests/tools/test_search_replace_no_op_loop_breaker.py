"""Regression test: search_replace no-op loop-breaker.

Stress run on 2026-04-28 observed write rate drop from 74% to 15%.
Admiral logs showed struggle:20-30:search_replace pattern — the model
made 20-30 consecutive tool calls without writing any file. Session
inspection revealed two consecutive search_replace calls on server.py
both returning "edited successfully (1 block(s), +0 line(s))." without
writing anything, because the SEARCH and REPLACE text produced identical
content.

The model saw "edited successfully" but the file was unchanged, so it
re-read the file, confirmed its edit "wasn't applied", and retried —
creating a loop that fired the struggle detector repeatedly.

Fix: when modified_content == original_content (search == replace, or
replacement is a no-op), return an ALREADY CORRECT advisory instead of
the ambiguous "edited successfully (+0 lines)" message, and return early
without writing. The model can then move on to the next task.
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
def ctx(tmp_path) -> InvokeContext:
    state: dict = {}
    return InvokeContext(tool_call_id="tc_1", read_file_state=state)


@pytest.fixture
def py_file(tmp_path: Path) -> Path:
    src = (
        "def register_method(self, name, func):\n"
        "    self.methods[name] = func\n"
        "\n"
        "def call_method(self, name, *args):\n"
        "    return self.methods[name](*args)\n"
    )
    p = tmp_path / "server.py"
    p.write_text(src)
    return p


async def _run_to_result(tool, args, ctx):
    last = None
    async for event in tool.run(args, ctx):
        last = event
    return last


def _make_noop_content(py_file: Path) -> str:
    """Build a SEARCH/REPLACE block where SEARCH == REPLACE (no-op)."""
    text = "    self.methods[name] = func"
    return (
        f"<<<<<<< SEARCH\n{text}\n=======\n{text}\n>>>>>>> REPLACE\n"
    )


@pytest.mark.asyncio
async def test_noop_edit_returns_already_correct(tool, py_file, ctx):
    """When SEARCH == REPLACE the result is ALREADY CORRECT, not 'edited successfully'."""
    # Pre-populate read state so Read-before-Edit passes
    ctx.read_file_state[str(py_file)] = {
        "content": py_file.read_text(),
        "timestamp": py_file.stat().st_mtime_ns,
        "offset": 0,
        "limit": None,
    }
    content = _make_noop_content(py_file)
    args = SearchReplaceArgs(file_path=str(py_file), content=content)

    result = await _run_to_result(tool, args, ctx)

    assert isinstance(result, SearchReplaceResult)
    assert "ALREADY CORRECT" in result.content, (
        f"Expected ALREADY CORRECT advisory, got: {result.content!r}"
    )
    assert result.lines_changed == 0


@pytest.mark.asyncio
async def test_noop_edit_does_not_write_file(tool, py_file, ctx):
    """A no-op search_replace must not modify the file on disk."""
    ctx.read_file_state[str(py_file)] = {
        "content": py_file.read_text(),
        "timestamp": py_file.stat().st_mtime_ns,
        "offset": 0,
        "limit": None,
    }
    mtime_before = py_file.stat().st_mtime_ns
    content = _make_noop_content(py_file)
    args = SearchReplaceArgs(file_path=str(py_file), content=content)
    await _run_to_result(tool, args, ctx)

    assert py_file.stat().st_mtime_ns == mtime_before, (
        "File was written even though content was unchanged"
    )


@pytest.mark.asyncio
async def test_noop_edit_text_not_in_file_returns_already_correct(tool, py_file, ctx):
    """When SEARCH == REPLACE and the text is NOT in the file, return ALREADY CORRECT early.

    Previously this would fall through to the 'search text not found' error path,
    causing the model to loop. With the early short-circuit, it returns ALREADY CORRECT
    regardless of whether the search text exists in the file.
    """
    ctx.read_file_state[str(py_file)] = {
        "content": py_file.read_text(),
        "timestamp": py_file.stat().st_mtime_ns,
        "offset": 0,
        "limit": None,
    }
    phantom_text = "    this_text_is_not_in_the_file = True"
    content = f"<<<<<<< SEARCH\n{phantom_text}\n=======\n{phantom_text}\n>>>>>>> REPLACE\n"
    args = SearchReplaceArgs(file_path=str(py_file), content=content)

    result = await _run_to_result(tool, args, ctx)

    assert isinstance(result, SearchReplaceResult)
    assert "ALREADY CORRECT" in result.content, (
        f"Expected ALREADY CORRECT advisory for byte-identical no-op, got: {result.content!r}"
    )
    assert result.lines_changed == 0


@pytest.mark.asyncio
async def test_real_edit_still_works(tool, py_file, ctx):
    """A genuine edit still succeeds and produces a non-zero lines_changed or new content."""
    original = py_file.read_text()
    ctx.read_file_state[str(py_file)] = {
        "content": original,
        "timestamp": py_file.stat().st_mtime_ns,
        "offset": 0,
        "limit": None,
    }
    content = (
        "<<<<<<< SEARCH\n"
        "    self.methods[name] = func\n"
        "=======\n"
        "    self.methods[name] = func  # registered\n"
        ">>>>>>> REPLACE\n"
    )
    args = SearchReplaceArgs(file_path=str(py_file), content=content)
    result = await _run_to_result(tool, args, ctx)

    assert isinstance(result, SearchReplaceResult)
    assert "edited successfully" in result.content
    assert "ALREADY CORRECT" not in result.content
    assert "registered" in py_file.read_text()

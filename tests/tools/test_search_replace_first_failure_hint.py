"""Regression test: search_replace embeds file head on first failure.

Admiral logs (2026-05-02) showed retry_after_error:search_replace events
where the model retried a failed search_replace without re-reading the file.
The file head was only embedded starting at count=2, so the model had no
context on the first failure and would blindly retry the same text.

Fix: embed a FILE HEAD hint on the first failure (count=1) so the model
can immediately see the actual file content and adjust its search text
without needing a second retry cycle.
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


@pytest.fixture
def tool():
    return SearchReplace(SearchReplaceConfig(), BaseToolState())


@pytest.fixture
def ctx() -> InvokeContext:
    # read_file_state=None bypasses Read-before-Write guard so tests can focus
    # on the "search text not found" hint behavior specifically.
    return InvokeContext(tool_call_id="tc_hint_1", read_file_state=None)


async def _run(tool, ctx, tmp_path, file_content, search_text, replace_text="NEW"):
    f = tmp_path / "target.py"
    f.write_text(file_content)
    args = SearchReplaceArgs(
        file_path=str(f),
        content=f"<<<<<<< SEARCH\n{search_text}\n=======\n{replace_text}\n>>>>>>> REPLACE",
    )
    last = None
    async for event in tool.run(args, ctx):
        last = event
    return last


def test_first_failure_embeds_file_head(tool, ctx, tmp_path):
    """On first search_replace failure, result includes file content hint."""
    content = "def foo():\n    pass\n\ndef bar():\n    return 42\n"
    result = asyncio.run(_run(tool, ctx, tmp_path, content, search_text="def nonexistent_func():"))
    assert isinstance(result, SearchReplaceResult)
    assert result.blocks_applied == 0
    assert "FILE HEAD" in result.content or "HINT" in result.content
    assert "def foo():" in result.content


def test_first_failure_hint_not_hard_stop(tool, ctx, tmp_path):
    """First failure uses soft HINT language, not HARD-STOP prohibition."""
    content = "x = 1\ny = 2\n"
    result = asyncio.run(_run(tool, ctx, tmp_path, content, search_text="z = 99"))
    assert isinstance(result, SearchReplaceResult)
    assert "HARD-STOP" not in result.content
    assert "HINT" in result.content or "FILE HEAD" in result.content


def test_second_failure_escalates(tool, ctx, tmp_path):
    """Second consecutive failure escalates to LOOP-BREAKER (existing behavior)."""
    content = "alpha = 1\nbeta = 2\n"
    f = tmp_path / "target.py"
    f.write_text(content)
    args = SearchReplaceArgs(
        file_path=str(f),
        content="<<<<<<< SEARCH\ngamma = 3\n=======\ndelta = 4\n>>>>>>> REPLACE",
    )

    async def run_twice():
        async for _ in tool.run(args, ctx):
            pass
        last = None
        async for event in tool.run(args, ctx):
            last = event
        return last

    result = asyncio.run(run_twice())
    assert isinstance(result, SearchReplaceResult)
    assert "LOOP-BREAKER" in result.content or "consecutive" in result.content


def test_success_resets_fail_counter(tool, ctx, tmp_path):
    """After a successful edit, the fail counter resets for that file."""
    content = "def hello():\n    pass\n"
    f = tmp_path / "target.py"
    f.write_text(content)

    async def run_scenario():
        # Fail once
        fail_args = SearchReplaceArgs(
            file_path=str(f),
            content="<<<<<<< SEARCH\nno_such_function():\n=======\nreplaced\n>>>>>>> REPLACE",
        )
        async for _ in tool.run(fail_args, ctx):
            pass

        # Succeed
        ok_args = SearchReplaceArgs(
            file_path=str(f),
            content="<<<<<<< SEARCH\ndef hello():\n=======\ndef goodbye():\n>>>>>>> REPLACE",
        )
        ok_result = None
        async for event in tool.run(ok_args, ctx):
            ok_result = event
        assert ok_result.blocks_applied == 1

        # Fail again — should be count=1 (reset), not count=2
        fail2_args = SearchReplaceArgs(
            file_path=str(f),
            content="<<<<<<< SEARCH\nstill_no_such_function():\n=======\nreplaced\n>>>>>>> REPLACE",
        )
        fail2_result = None
        async for event in tool.run(fail2_args, ctx):
            fail2_result = event
        return fail2_result

    result = asyncio.run(run_scenario())
    assert "HINT" in result.content or "FILE HEAD" in result.content
    assert "LOOP-BREAKER" not in result.content

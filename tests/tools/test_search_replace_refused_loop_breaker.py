"""Regression test: search_replace REFUSED-raw loop-breaker.

Stress run on 2026-04-25 showed Gemma 4 retrying the SAME
search_replace raw-content call after getting a REFUSED error
(append-would-break-syntax path). Admiral logs counted 14
`retry_after_error:search_replace` fires per 6h, all with the same
"REFUSED: the raw content" head. The error message had actionable
hints but the model ignored them.

Fix: track consecutive REFUSED-raw failures per file in tool state.
On 2nd+ consecutive REFUSED, embed the actual file head/tail in the
error so the model sees current state and can't keep retrying with
stale assumptions. Mirrors the existing "Search text not found"
loop-breaker at line 310 of search_replace.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from drydock.core.tools.base import BaseToolState, InvokeContext, ToolError
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


@pytest.fixture
def big_broken_py(tmp_path: Path) -> Path:
    """A file that ends with a class-body so an appended top-level def
    would break Python syntax (forces the REFUSED path)."""
    src = "# big module\n"
    for i in range(60):
        src += f"\n\ndef existing_{i}(x):\n    return x * {i}\n"
    src += "\n\nclass HoldsState:\n    def keep(self):\n        return (\n"
    p = tmp_path / "tools.py"
    p.write_text(src)
    assert p.stat().st_size > 500
    return p


async def _run_to_result(tool, args, ctx):
    last = None
    async for event in tool.run(args, ctx):
        last = event
    return last


@pytest.mark.asyncio
async def test_first_refused_is_short(tool, big_broken_py, ctx):
    """First REFUSED uses the original short message — no head dump yet."""
    raw = "def is_prime(n):\n    return True\n"
    args = SearchReplaceArgs(file_path=str(big_broken_py), content=raw)

    with pytest.raises(ToolError) as excinfo:
        await _run_to_result(tool, args, ctx)

    msg = str(excinfo.value)
    assert "REFUSED" in msg
    assert "LOOP-BREAKER" not in msg
    assert "FILE HEAD" not in msg


@pytest.mark.asyncio
async def test_second_refused_embeds_file_head(tool, big_broken_py, ctx):
    """Second consecutive REFUSED on same file → embeds actual file head
    plus an escalated directive so the model sees fresh context."""
    raw = "def is_prime(n):\n    return True\n"
    args = SearchReplaceArgs(file_path=str(big_broken_py), content=raw)

    with pytest.raises(ToolError):
        await _run_to_result(tool, args, ctx)
    with pytest.raises(ToolError) as excinfo:
        await _run_to_result(tool, args, ctx)

    msg = str(excinfo.value)
    assert "LOOP-BREAKER" in msg
    assert "#2 consecutive REFUSED" in msg
    assert "FILE HEAD" in msg
    assert "def existing_0(x)" in msg  # actual file content embedded
    assert "write_file" in msg
    assert "overwrite=True" in msg


@pytest.mark.asyncio
async def test_third_refused_keeps_loop_breaker(tool, big_broken_py, ctx):
    """Third REFUSED still has the loop-breaker (count keeps incrementing)."""
    raw = "def is_prime(n):\n    return True\n"
    args = SearchReplaceArgs(file_path=str(big_broken_py), content=raw)

    for _ in range(3):
        with pytest.raises(ToolError):
            await _run_to_result(tool, args, ctx)

    with pytest.raises(ToolError) as excinfo:
        await _run_to_result(tool, args, ctx)
    msg = str(excinfo.value)
    assert "#4 consecutive REFUSED" in msg


@pytest.mark.asyncio
async def test_refused_count_resets_after_successful_append(
    tool, tmp_path: Path, ctx
):
    """Successful APPEND clears the REFUSED counter — next REFUSED on the
    same file starts fresh (no LOOP-BREAKER on first failure after recovery)."""
    target = tmp_path / "data.txt"
    target.write_text("line1\nline2\nline3\n" * 100)  # >500 bytes, .txt → no AST check

    # First call: append succeeds (non-py never breaks syntax).
    raw_ok = "appended_marker_line that is long enough to clear the 20-char gate\n"
    result = await _run_to_result(
        tool, SearchReplaceArgs(file_path=str(target), content=raw_ok), ctx
    )
    assert isinstance(result, SearchReplaceResult)

    # Now switch to a .py file and trigger one REFUSED — should be the
    # first-time short message because counter was reset by the success.
    py_target = tmp_path / "tools.py"
    py_src = "# header\n"
    for i in range(60):
        py_src += f"\n\ndef e_{i}(x):\n    return x\n"
    py_src += "\n\nclass C:\n    def m(self):\n        return (\n"
    py_target.write_text(py_src)

    with pytest.raises(ToolError) as excinfo:
        await _run_to_result(
            tool, SearchReplaceArgs(file_path=str(py_target), content="def x(): return 1\n"), ctx
        )
    assert "LOOP-BREAKER" not in str(excinfo.value)

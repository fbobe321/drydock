"""Regression test: search_replace APPEND fallback for short raw content.

Stress run on 2026-04-25 caught Gemma 4 stuck in a loop on
`search_replace` calls that sent only a new function (no SEARCH/REPLACE
blocks) — the safety check refused the overwrite ("would shrink file by
N%"), the model retried the same call, and the prompt was lost. Model
intent in those cases is to APPEND, not rewrite. New behavior: when the
combined (existing + raw) text parses cleanly, append it; only refuse
when an append would break the syntax.
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
def big_existing_py(tmp_path: Path) -> Path:
    """A 2000+ char Python file the model would consider 'done'."""
    src = "# big existing module\n"
    for i in range(50):
        src += f"\n\ndef existing_{i}(x):\n    return x * {i}\n"
    p = tmp_path / "tools.py"
    p.write_text(src)
    return p


@pytest.fixture
def ctx() -> InvokeContext:
    return InvokeContext(tool_call_id="tc_1", read_file_state={})


async def _run_to_result(tool, args, ctx):
    last = None
    async for event in tool.run(args, ctx):
        last = event
    return last


@pytest.mark.asyncio
async def test_append_when_combined_parses(tool, big_existing_py, ctx):
    """Raw content that's a valid new def → append onto the existing file."""
    raw = "def is_prime(n):\n    if n < 2:\n        return False\n    return all(n % i for i in range(2, int(n**0.5)+1))\n"
    args = SearchReplaceArgs(file_path=str(big_existing_py), content=raw)

    result = await _run_to_result(tool, args, ctx)

    assert isinstance(result, SearchReplaceResult), f"got {type(result).__name__}: {result}"
    assert result.blocks_applied == 1
    assert any("Appended" in w for w in result.warnings)

    final = big_existing_py.read_text()
    assert "def is_prime(n):" in final
    assert "def existing_0(x):" in final  # existing kept
    assert "def existing_49(x):" in final


@pytest.mark.asyncio
async def test_refuses_when_append_would_break_syntax(tool, big_existing_py, ctx):
    """Raw content that would yield a SyntaxError when appended → refuse."""
    raw = "def broken(x:\n  return"  # invalid python
    args = SearchReplaceArgs(file_path=str(big_existing_py), content=raw)

    with pytest.raises(ToolError) as excinfo:
        await _run_to_result(tool, args, ctx)

    msg = str(excinfo.value)
    assert "REFUSED" in msg
    assert "syntax" in msg.lower()
    # Existing file untouched
    final = big_existing_py.read_text()
    assert "broken(x:" not in final


@pytest.mark.asyncio
async def test_non_python_short_raw_appends(tool, tmp_path: Path, ctx):
    """For non-.py files we don't AST-check — append always wins (no
    syntax to break). Repro of the loop pattern: model sends a small
    fragment, file is much larger, append succeeds."""
    target = tmp_path / "data.txt"
    target.write_text("line1\nline2\nline3\nline4\n" * 80)  # ~2000 bytes
    # Need >20 chars to enter the fallback branch.
    raw = "appended_marker_line that is long enough to clear the 20-char gate\n"
    args = SearchReplaceArgs(file_path=str(target), content=raw)

    result = await _run_to_result(tool, args, ctx)
    assert isinstance(result, SearchReplaceResult)
    final = target.read_text()
    assert "appended_marker_line" in final
    assert "line1" in final  # existing preserved


@pytest.mark.asyncio
async def test_small_existing_file_still_overwrites(tool, tmp_path: Path, ctx):
    """Existing < 500 bytes — pre-existing behavior (full overwrite) is
    preserved because the safety check only fires above 500 bytes."""
    target = tmp_path / "tiny.py"
    target.write_text("x = 1\n")
    # >20 chars so fallback fires; existing<500 so overwrite path runs.
    raw = "y = 2\nz = 3\nlong_enough_to_clear_the_20_char_gate = 1\n"
    args = SearchReplaceArgs(file_path=str(target), content=raw)

    result = await _run_to_result(tool, args, ctx)
    assert isinstance(result, SearchReplaceResult)
    final = target.read_text()
    # Existing < 500 bytes, so OVERWRITE path runs (not append).
    assert final.strip() == raw.strip()

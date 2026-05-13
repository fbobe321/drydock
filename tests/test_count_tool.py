"""Tests for the built-in count tool (drydock.core.tools.builtins.count_tool)."""
from __future__ import annotations

from pathlib import Path

import pytest

from drydock.core.tools.builtins.count_tool import (
    Count,
    CountArgs,
    CountResult,
)


async def _run(**kwargs) -> CountResult:
    args = CountArgs(**kwargs)
    tool = Count.__new__(Count)
    tool.config = type("_C", (), {"permission": None})()
    out: CountResult | None = None
    async for ev in tool.run(args):
        if isinstance(ev, CountResult):
            out = ev
    assert out is not None
    return out


# ============================================================================
# Substring modes
# ============================================================================

class TestSubstring:
    @pytest.mark.asyncio
    async def test_strawberry_r(self):
        out = await _run(pattern="r", text="strawberry")
        assert out.ok and out.count == 3 and out.mode == "substring"

    @pytest.mark.asyncio
    async def test_no_match(self):
        out = await _run(pattern="z", text="strawberry")
        assert out.ok and out.count == 0

    @pytest.mark.asyncio
    async def test_overlapping_not_double_counted(self):
        # Python str.count does NOT count overlaps — "aa" in "aaa" = 1.
        out = await _run(pattern="aa", text="aaa")
        assert out.ok and out.count == 1

    @pytest.mark.asyncio
    async def test_case_sensitive_distinct(self):
        out = await _run(pattern="R", text="Rrrr")
        assert out.ok and out.count == 1

    @pytest.mark.asyncio
    async def test_substring_ci(self):
        out = await _run(mode="substring_ci", pattern="r", text="Rrrr")
        assert out.ok and out.count == 4

    @pytest.mark.asyncio
    async def test_missing_pattern_returns_error(self):
        out = await _run(text="hello")
        assert not out.ok and "pattern" in out.error.lower()


# ============================================================================
# Regex
# ============================================================================

class TestRegex:
    @pytest.mark.asyncio
    async def test_simple(self):
        out = await _run(mode="regex", pattern=r"\d+", text="a1 b22 c333")
        assert out.ok and out.count == 3

    @pytest.mark.asyncio
    async def test_anchor(self):
        out = await _run(
            mode="regex",
            pattern=r"^def\s",
            text="def foo():\n  pass\n  def bar():\n    pass\n",
        )
        # Multiline default OFF — only the first "def " at string start counts.
        # User who wants multiline can pass (?m).
        assert out.ok and out.count == 1

    @pytest.mark.asyncio
    async def test_invalid_regex(self):
        out = await _run(mode="regex", pattern="[unclosed", text="x")
        assert not out.ok and "invalid regex" in out.error.lower()

    @pytest.mark.asyncio
    async def test_missing_pattern(self):
        out = await _run(mode="regex", text="hi")
        assert not out.ok


# ============================================================================
# Pattern-free modes
# ============================================================================

class TestPatternFree:
    @pytest.mark.asyncio
    async def test_lines_three_with_trailing_nl(self):
        out = await _run(mode="lines", text="a\nb\nc\n")
        assert out.ok and out.count == 3

    @pytest.mark.asyncio
    async def test_lines_three_without_trailing_nl(self):
        out = await _run(mode="lines", text="a\nb\nc")
        assert out.ok and out.count == 3

    @pytest.mark.asyncio
    async def test_lines_empty(self):
        out = await _run(mode="lines", text="")
        assert out.ok and out.count == 0

    @pytest.mark.asyncio
    async def test_words(self):
        out = await _run(mode="words", text="hello   world\nfoo")
        assert out.ok and out.count == 3

    @pytest.mark.asyncio
    async def test_chars(self):
        out = await _run(mode="chars", text="héllo")
        assert out.ok and out.count == 5

    @pytest.mark.asyncio
    async def test_bytes_utf8(self):
        # é is 2 bytes in UTF-8 → "héllo" = 6 bytes
        out = await _run(mode="bytes", text="héllo")
        assert out.ok and out.count == 6


# ============================================================================
# File source
# ============================================================================

class TestFileSource:
    @pytest.mark.asyncio
    async def test_file_substring(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("foo bar foo baz foo\n")
        out = await _run(pattern="foo", path=str(f))
        assert out.ok and out.count == 3 and out.source == str(f)

    @pytest.mark.asyncio
    async def test_file_lines(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("a\nb\nc\nd\n")
        out = await _run(mode="lines", path=str(f))
        assert out.ok and out.count == 4

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path: Path):
        out = await _run(pattern="x", path=str(tmp_path / "nope.txt"))
        assert not out.ok and "not found" in out.error.lower()

    @pytest.mark.asyncio
    async def test_file_too_large(self, tmp_path: Path, monkeypatch):
        from drydock.core.tools.builtins import count_tool
        monkeypatch.setattr(count_tool, "_MAX_FILE_BYTES", 100)
        f = tmp_path / "big.txt"
        f.write_text("x" * 200)
        out = await _run(pattern="x", path=str(f))
        assert not out.ok and "too large" in out.error.lower()

    @pytest.mark.asyncio
    async def test_text_and_path_both_rejected(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("y")
        out = await _run(pattern="y", text="y", path=str(f))
        assert not out.ok and "either" in out.error.lower()

    @pytest.mark.asyncio
    async def test_neither_text_nor_path(self):
        # Empty defaults are accepted as the empty-string source. Substring
        # mode still requires a pattern; missing one fires the pattern error.
        out = await _run(pattern="")
        # mode is `substring` by default → empty pattern → error.
        assert not out.ok and "pattern" in out.error.lower()


# ============================================================================
# Discovery
# ============================================================================

def test_count_tool_name():
    assert Count.get_name() == "count"

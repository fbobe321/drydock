"""Tests for the verify tool — bash + file expectation checks."""
from __future__ import annotations

from pathlib import Path

import pytest

from drydock.core.tools.builtins.verify_tool import (
    Verify,
    VerifyArgs,
    VerifyResult,
)


async def _run(**kwargs) -> VerifyResult:
    args = VerifyArgs(**kwargs)
    tool = Verify.__new__(Verify)
    tool.config = type("_C", (), {"permission": None})()
    out: VerifyResult | None = None
    async for ev in tool.run(args):
        if isinstance(ev, VerifyResult):
            out = ev
    assert out is not None
    return out


# ============================================================================
# contains / not_contains / equals / regex
# ============================================================================

class TestContains:
    @pytest.mark.asyncio
    async def test_contains_pass(self):
        out = await _run(criterion="echo hello", command="echo hello world",
                         expect="hello", expect_mode="contains")
        assert out.ok and out.passed
        assert out.exit_code == 0

    @pytest.mark.asyncio
    async def test_contains_fail(self):
        out = await _run(criterion="echo hello", command="echo hello world",
                         expect="goodbye", expect_mode="contains")
        assert out.ok and not out.passed

    @pytest.mark.asyncio
    async def test_not_contains_pass(self):
        out = await _run(criterion="no errors", command="echo hello",
                         expect="ERROR", expect_mode="not_contains")
        assert out.ok and out.passed

    @pytest.mark.asyncio
    async def test_not_contains_fail(self):
        out = await _run(criterion="no errors", command="echo ERROR x",
                         expect="ERROR", expect_mode="not_contains")
        assert out.ok and not out.passed

    @pytest.mark.asyncio
    async def test_equals_pass(self):
        out = await _run(criterion="exact", command="printf 'abc'",
                         expect="abc", expect_mode="equals")
        assert out.ok and out.passed

    @pytest.mark.asyncio
    async def test_equals_fail(self):
        out = await _run(criterion="exact", command="echo 'abc def'",
                         expect="abc", expect_mode="equals")
        assert out.ok and not out.passed

    @pytest.mark.asyncio
    async def test_regex_pass(self):
        out = await _run(criterion="number in output", command="echo abc 42 def",
                         expect=r"\d+", expect_mode="regex")
        assert out.ok and out.passed

    @pytest.mark.asyncio
    async def test_regex_fail(self):
        out = await _run(criterion="number", command="echo no digits",
                         expect=r"\d+", expect_mode="regex")
        assert out.ok and not out.passed

    @pytest.mark.asyncio
    async def test_regex_invalid(self):
        out = await _run(criterion="x", command="echo y",
                         expect="[unclosed", expect_mode="regex")
        assert not out.ok and "regex" in out.error.lower()


# ============================================================================
# exit_code
# ============================================================================

class TestExitCode:
    @pytest.mark.asyncio
    async def test_exit_code_zero(self):
        out = await _run(criterion="success", command="true",
                         expect="0", expect_mode="exit_code")
        assert out.ok and out.passed and out.exit_code == 0

    @pytest.mark.asyncio
    async def test_exit_code_nonzero_pass(self):
        out = await _run(criterion="exits 3", command="exit 3",
                         expect="3", expect_mode="exit_code")
        assert out.ok and out.passed and out.exit_code == 3

    @pytest.mark.asyncio
    async def test_exit_code_mismatch(self):
        out = await _run(criterion="exit 0 expected", command="false",
                         expect="0", expect_mode="exit_code")
        assert out.ok and not out.passed and out.exit_code == 1

    @pytest.mark.asyncio
    async def test_exit_code_non_integer(self):
        out = await _run(criterion="x", command="true",
                         expect="abc", expect_mode="exit_code")
        assert not out.ok and "integer" in out.error.lower()


# ============================================================================
# file_exists / file_contains
# ============================================================================

class TestFileModes:
    @pytest.mark.asyncio
    async def test_file_exists_pass(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("hi")
        out = await _run(criterion="file there", expect=str(f),
                         expect_mode="file_exists")
        assert out.ok and out.passed

    @pytest.mark.asyncio
    async def test_file_exists_fail(self, tmp_path: Path):
        out = await _run(criterion="file there",
                         expect=str(tmp_path / "nope.txt"),
                         expect_mode="file_exists")
        assert out.ok and not out.passed

    @pytest.mark.asyncio
    async def test_file_exists_missing_expect(self):
        out = await _run(criterion="x", expect_mode="file_exists")
        assert not out.ok

    @pytest.mark.asyncio
    async def test_file_contains_pass(self, tmp_path: Path):
        f = tmp_path / "x.md"
        f.write_text("# Header\n\n## Math Tool\n\nDetails.")
        out = await _run(criterion="readme has section",
                         expect=f"{f}::## Math Tool",
                         expect_mode="file_contains")
        assert out.ok and out.passed

    @pytest.mark.asyncio
    async def test_file_contains_fail(self, tmp_path: Path):
        f = tmp_path / "x.md"
        f.write_text("nothing relevant here")
        out = await _run(criterion="readme has section",
                         expect=f"{f}::## Math Tool",
                         expect_mode="file_contains")
        assert out.ok and not out.passed

    @pytest.mark.asyncio
    async def test_file_contains_missing_separator(self):
        out = await _run(criterion="x", expect="no_separator_here",
                         expect_mode="file_contains")
        assert not out.ok and "PATH::SUBSTRING" in out.error

    @pytest.mark.asyncio
    async def test_file_contains_missing_file(self, tmp_path: Path):
        out = await _run(criterion="x", expect=f"{tmp_path / 'nope.txt'}::needle",
                         expect_mode="file_contains")
        assert out.ok and not out.passed
        assert "missing" in out.reason.lower()


# ============================================================================
# Edge cases
# ============================================================================

class TestEdges:
    @pytest.mark.asyncio
    async def test_command_required_for_shell_modes(self):
        out = await _run(criterion="x", expect="hi", expect_mode="contains")
        assert not out.ok and "command required" in out.error.lower()

    @pytest.mark.asyncio
    async def test_expect_required_for_contains(self):
        out = await _run(criterion="x", command="echo hi",
                         expect_mode="contains")
        assert not out.ok and "expect required" in out.error.lower()

    @pytest.mark.asyncio
    async def test_timeout_returns_124(self):
        out = await _run(
            criterion="quick", command="sleep 5",
            expect="never", expect_mode="contains", timeout=1,
        )
        assert out.ok and not out.passed
        assert out.exit_code == 124


# ============================================================================
# Discovery
# ============================================================================

def test_verify_tool_name():
    assert Verify.get_name() == "verify"

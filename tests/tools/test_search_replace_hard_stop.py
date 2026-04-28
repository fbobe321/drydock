"""Regression test: search_replace hard-stop on 3rd consecutive failure.

Admiral logs (2026-04-28) showed `loop:search_replace` firing with
identical args at count 3+ — the model kept retrying the same SEARCH
text even after the loop-breaker embedded the file head at count 2.

Fix: at count >= 3 escalate to a HARD-STOP message that (a) shows the
full file content (up to 4000 chars), and (b) explicitly forbids further
search_replace retries and requires write_file instead.
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
def py_file(tmp_path: Path) -> Path:
    src = (
        "from .base import BasePlugin\n"
        "from .registry import PluginRegistry\n"
        "\n"
        "class ToolAgent:\n"
        "    def run(self):\n"
        "        pass\n"
    )
    p = tmp_path / "agent.py"
    p.write_text(src)
    return p


def _ctx_with_file(path: Path) -> InvokeContext:
    state: dict = {str(path): {"timestamp": path.stat().st_mtime_ns}}
    return InvokeContext(tool_call_id="tc_1", read_file_state=state)


async def _run_to_result(tool, args, ctx) -> SearchReplaceResult:
    last = None
    async for event in tool.run(args, ctx):
        last = event
    assert isinstance(last, SearchReplaceResult)
    return last


@pytest.mark.asyncio
async def test_count2_shows_loop_breaker(tool, py_file):
    """Second consecutive failure shows LOOP-BREAKER with file head."""
    ctx = _ctx_with_file(py_file)
    missing = "    from .oci_image_tool import OCI\n"
    args = SearchReplaceArgs(
        file_path=str(py_file),
        content=f"<<<<<<< SEARCH\n{missing}=======\n{missing}    from .singularity import Singularity\n>>>>>>> REPLACE",
    )
    # First failure — no loop-breaker yet
    r1 = await _run_to_result(tool, args, ctx)
    assert r1.blocks_applied == 0
    assert "LOOP-BREAKER" not in r1.content

    # Second failure — loop-breaker fires
    r2 = await _run_to_result(tool, args, ctx)
    assert r2.blocks_applied == 0
    assert "LOOP-BREAKER" in r2.content
    # File head is embedded
    assert "from .base import BasePlugin" in r2.content


@pytest.mark.asyncio
async def test_count3_shows_hard_stop(tool, py_file):
    """Third consecutive failure escalates to HARD-STOP with full file and prohibition."""
    ctx = _ctx_with_file(py_file)
    missing = "    from .oci_image_tool import OCI\n"
    args = SearchReplaceArgs(
        file_path=str(py_file),
        content=f"<<<<<<< SEARCH\n{missing}=======\n{missing}    from .singularity import Singularity\n>>>>>>> REPLACE",
    )
    await _run_to_result(tool, args, ctx)  # count 1
    await _run_to_result(tool, args, ctx)  # count 2
    r3 = await _run_to_result(tool, args, ctx)  # count 3

    assert r3.blocks_applied == 0
    assert "HARD-STOP" in r3.content
    # Explicitly forbids retrying
    assert "DO NOT retry" in r3.content
    # Directs to write_file
    assert "write_file" in r3.content
    # Full file content is embedded
    assert "ToolAgent" in r3.content


@pytest.mark.asyncio
async def test_success_resets_counter(tool, py_file):
    """A successful edit resets the failure counter so a later failure starts fresh."""
    ctx = _ctx_with_file(py_file)
    missing = "    from .oci_image_tool import OCI\n"
    bad_args = SearchReplaceArgs(
        file_path=str(py_file),
        content=f"<<<<<<< SEARCH\n{missing}=======\n{missing}    from .singularity import Singularity\n>>>>>>> REPLACE",
    )
    # Two failures to prime the counter
    await _run_to_result(tool, bad_args, ctx)
    await _run_to_result(tool, bad_args, ctx)

    # A successful edit resets the counter
    good_args = SearchReplaceArgs(
        file_path=str(py_file),
        content=(
            "<<<<<<< SEARCH\n"
            "class ToolAgent:\n"
            "=======\n"
            "class ToolAgent:  # updated\n"
            ">>>>>>> REPLACE"
        ),
    )
    ctx2 = _ctx_with_file(py_file)
    r_ok = await _run_to_result(tool, good_args, ctx2)
    assert r_ok.blocks_applied == 1

    # The next failure should be count=1, not count=3 (no HARD-STOP)
    ctx3 = _ctx_with_file(py_file)
    r_after = await _run_to_result(tool, bad_args, ctx3)
    assert "HARD-STOP" not in r_after.content
    assert "LOOP-BREAKER" not in r_after.content

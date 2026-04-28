"""Regression test: write_file dedup message includes missing imports.

Stress run on 2026-04-27 showed Gemma 4 re-writing __main__.py 7+ times
even after Admiral's canned loop-breaker fired. Root cause: the dedup
advisory said "write a DIFFERENT file" but didn't specify WHICH file.
__main__.py imported server.py and cli.py that didn't exist yet, but the
dedup path skipped the _check_missing_sibling_imports check.

Fix: in the dedup path (repeat_n >= 2), parse the file content and append
missing imports to the advisory so the model knows exactly what to write.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.write_file import (
    WriteFile,
    WriteFileArgs,
    WriteFileConfig,
    WriteFileResult,
)


def _make_tool(tmp_path: Path) -> WriteFile:
    config = WriteFileConfig(allowed_dirs=[str(tmp_path)])
    state = BaseToolState()
    tool = WriteFile(config=config, state=state)
    return tool


async def _invoke(tool: WriteFile, path: str, content: str) -> WriteFileResult:
    args = WriteFileArgs(path=path, content=content, overwrite=True)
    results = []
    async for event in tool.run(args, None):
        if isinstance(event, WriteFileResult):
            results.append(event)
    assert results, "expected a WriteFileResult"
    return results[-1]


@pytest.mark.asyncio
async def test_dedup_advisory_includes_missing_imports(tmp_path: Path) -> None:
    """After 2+ identical writes, the advisory names missing imported modules."""
    tool = _make_tool(tmp_path)
    pkg = tmp_path / "mypkg"
    pkg.mkdir()

    main_content = (
        "from .server import Server\n"
        "from .cli import CLI\n"
        "\n"
        "def main():\n"
        "    s = Server()\n"
        "    CLI(s).run()\n"
    )
    main_path = str(pkg / "__main__.py")

    # First write: file doesn't exist yet — succeeds normally.
    r1 = await _invoke(tool, main_path, main_content)
    assert r1.bytes_written > 0, "first write should succeed"

    # Second write: same content — advisory, no missing-imports hint yet (repeat_n=1).
    r2 = await _invoke(tool, main_path, main_content)
    assert r2.bytes_written == 0, "dedup should be a no-op"
    assert "no-op" in (r2.content or "").lower()

    # Third write: repeat_n=2 — should include missing-import hint.
    r3 = await _invoke(tool, main_path, main_content)
    assert r3.bytes_written == 0, "dedup should be a no-op"
    msg = r3.content or ""
    assert "server" in msg.lower() or "cli" in msg.lower(), (
        f"Expected missing imports named in dedup advisory, got: {msg!r}"
    )


@pytest.mark.asyncio
async def test_dedup_no_false_positive_when_imports_exist(tmp_path: Path) -> None:
    """If the imported modules already exist on disk, don't mention them."""
    tool = _make_tool(tmp_path)
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    # Create the sibling files first
    (pkg / "server.py").write_text("class Server: pass\n")
    (pkg / "cli.py").write_text("class CLI: pass\n")

    main_content = (
        "from .server import Server\n"
        "from .cli import CLI\n\n"
        "def main(): CLI(Server()).run()\n"
    )
    main_path = str(pkg / "__main__.py")

    await _invoke(tool, main_path, main_content)
    await _invoke(tool, main_path, main_content)
    r3 = await _invoke(tool, main_path, main_content)
    msg = r3.content or ""
    # Missing-import suffix should NOT appear when siblings exist
    assert "don't exist yet" not in msg, (
        f"Should not flag existing siblings as missing, got: {msg!r}"
    )

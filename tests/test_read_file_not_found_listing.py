"""Regression test: read_file includes parent directory listing on file-not-found.

Admiral logs showed 'retry_after_error:read_file' firing when the model retries
a missing-file path instead of checking what files actually exist nearby.
Fix: _not_found_msg() lists the parent directory so the model sees valid
neighbours and picks the right one without needing the admiral to intervene.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from drydock.core.tools.base import BaseToolState, ToolError
from drydock.core.tools.builtins.read_file import ReadFile, ReadFileArgs, ReadFileToolConfig


def _make_tool(tmp_path: Path) -> ReadFile:
    config = ReadFileToolConfig()
    state = BaseToolState()
    return ReadFile(config=config, state=state)


def test_not_found_includes_parent_listing(tmp_path: Path) -> None:
    """Error message includes sibling files when path does not exist."""
    (tmp_path / "server.py").write_text("# server")
    (tmp_path / "cli.py").write_text("# cli")
    missing = tmp_path / "nonexistent.py"

    tool = _make_tool(tmp_path)
    msg = tool._not_found_msg(missing)

    assert "File not found" in msg
    assert "server.py" in msg
    assert "cli.py" in msg


def test_not_found_empty_dir(tmp_path: Path) -> None:
    """Empty parent directory: still returns a 'File not found' message."""
    missing = tmp_path / "ghost.py"
    tool = _make_tool(tmp_path)
    msg = tool._not_found_msg(missing)
    assert "File not found" in msg


def test_not_found_truncates_long_listing(tmp_path: Path) -> None:
    """Listings longer than 30 entries are truncated with a '... (N more)' suffix."""
    for i in range(35):
        (tmp_path / f"file_{i:02d}.py").write_text("")
    missing = tmp_path / "missing.py"

    tool = _make_tool(tmp_path)
    msg = tool._not_found_msg(missing)

    assert "more)" in msg


def test_validate_path_raises_with_listing(tmp_path: Path, monkeypatch) -> None:
    """_validate_path raises ToolError whose message includes directory listing."""
    (tmp_path / "sibling.py").write_text("x=1")
    missing = tmp_path / "nope.py"

    tool = _make_tool(tmp_path)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ToolError) as exc_info:
        tool._validate_path(missing)

    assert "sibling.py" in str(exc_info.value)

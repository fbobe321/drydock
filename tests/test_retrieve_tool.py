"""Tests for the agent-facing Retrieve tool."""
from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.retrieve import (
    Retrieve,
    RetrieveArgs,
    RetrieveConfig,
    RetrieveResult,
    _resolve_db_path,
)


def _drive(tool: Retrieve, args: RetrieveArgs) -> RetrieveResult:
    """Run the async generator to completion and return the final result."""
    async def go() -> RetrieveResult:
        result = None
        async for event in tool.run(args):
            if isinstance(event, RetrieveResult):
                result = event
        assert result is not None
        return result
    return asyncio.run(go())


def test_no_index_in_non_project_returns_nudge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """No project markers in cwd → tool refuses to auto-ingest, returns hint."""
    monkeypatch.chdir(tmp_path)
    tool = Retrieve(config=RetrieveConfig(), state=BaseToolState())
    args = RetrieveArgs(query="anything", db=str(tmp_path / "nope.sqlite"))
    result = _drive(tool, args)
    assert result.found is False
    assert result.note == "not_a_project"
    assert "doesn't look like a project" in result.formatted


def test_auto_ingest_when_cwd_is_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Project markers present + no index → tool auto-ingests cwd, then
    answers the query in one shot."""
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / ".git").mkdir()  # marker
    (proj / "main.py").write_text(
        'class Widget:\n    """W."""\n    pass\n'
    )
    monkeypatch.chdir(proj)
    db_path = tmp_path / "auto.sqlite"

    tool = Retrieve(config=RetrieveConfig(), state=BaseToolState())
    args = RetrieveArgs(query="Widget", db=str(db_path))
    result = _drive(tool, args)

    assert result.found is True
    assert "Auto-indexed" in result.formatted
    assert "Widget" in result.formatted
    assert db_path.is_file()


def test_retrieve_returns_symbols_and_text(tmp_path: Path):
    """End-to-end: ingest a tiny project, query through the tool."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "core.py").write_text(textwrap.dedent('''
        class Widget:
            """A widget."""

        def make_widget():
            """Factory."""
            return Widget()
    '''))
    (proj / "design.md").write_text(textwrap.dedent("""
        # Widget design

        Widgets are immutable. Construction goes through `make_widget`.
    """).strip())

    db_path = tmp_path / "g.sqlite"
    from drydock.graphrag import Index
    Index(db_path).ingest_path(proj)

    tool = Retrieve(config=RetrieveConfig(), state=BaseToolState())
    args = RetrieveArgs(query="Widget", db=str(db_path))
    result = _drive(tool, args)

    assert result.found is True
    assert result.symbol_count >= 1
    assert "Widget" in result.formatted
    # Citation IDs should be present so the agent can quote them.
    assert "core.py" in result.formatted


def test_resolve_db_path_prefers_explicit_arg(tmp_path: Path):
    explicit = tmp_path / "explicit.sqlite"
    assert _resolve_db_path(str(explicit)) == explicit


def test_resolve_db_path_falls_back_to_user_home(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DRYDOCK_GRAPHRAG_DB", raising=False)
    # cwd has no .drydock/graphrag.sqlite during a normal test run.
    p = _resolve_db_path("")
    assert str(p).endswith(".drydock/graphrag.sqlite")


def test_resolve_db_path_uses_env_when_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    target = tmp_path / "env.sqlite"
    monkeypatch.setenv("DRYDOCK_GRAPHRAG_DB", str(target))
    assert _resolve_db_path("") == target


def test_fallback_db_kicks_in_when_primary_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """When primary index returns no hits, the tool falls through to
    DRYDOCK_GRAPHRAG_FALLBACK_DB if it has matching content. This
    closes the 2026-05-14 HLE-eval gap where 77% of model-invoked
    retrievals on STEM topics returned nothing useful."""
    from drydock.graphrag import Index

    # Primary db: empty corpus (no Widget content)
    primary = tmp_path / "primary.sqlite"
    Index(primary)  # creates schema

    # Fallback db: has Widget content
    fallback = tmp_path / "fallback.sqlite"
    proj = tmp_path / "fallback_src"
    proj.mkdir()
    (proj / "thing.py").write_text(
        'class Widget:\n    """The widget."""\n    pass\n'
    )
    Index(fallback).ingest_path(proj)

    monkeypatch.setenv("DRYDOCK_GRAPHRAG_FALLBACK_DB", str(fallback))

    tool = Retrieve(config=RetrieveConfig(), state=BaseToolState())
    args = RetrieveArgs(query="Widget", db=str(primary))  # explicit override
    # With explicit db, fallback should NOT kick in.
    result = _drive(tool, args)
    assert result.found is False  # primary is empty, explicit db skips fallback


def test_fallback_db_fires_when_db_not_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Without an explicit args.db, primary-empty → fallback fires."""
    from drydock.graphrag import Index

    primary = tmp_path / "primary.sqlite"
    Index(primary)  # empty

    fallback = tmp_path / "fallback.sqlite"
    proj = tmp_path / "fb_src"
    proj.mkdir()
    (proj / "thing.py").write_text(
        'class Widget:\n    """The widget."""\n    pass\n'
    )
    Index(fallback).ingest_path(proj)

    monkeypatch.setenv("DRYDOCK_GRAPHRAG_DB", str(primary))
    monkeypatch.setenv("DRYDOCK_GRAPHRAG_FALLBACK_DB", str(fallback))

    tool = Retrieve(config=RetrieveConfig(), state=BaseToolState())
    args = RetrieveArgs(query="Widget")  # no explicit db
    result = _drive(tool, args)
    assert result.found is True
    assert "fallback" in result.formatted.lower()
    assert "Widget" in result.formatted


def test_tool_is_discoverable():
    """ToolManager must auto-discover the new tool by name."""
    from drydock.core.paths import DEFAULT_TOOL_DIR
    from drydock.core.tools.manager import ToolManager
    classes = list(ToolManager._iter_tool_classes([DEFAULT_TOOL_DIR.path]))
    names = {c.get_name() for c in classes}
    assert "retrieve" in names


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

"""Tests for context_recovery — auto-fixup when a tool fails for lack of context."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from drydock.core.context_recovery import (
    recover_for_grep,
    recover_for_query,
    recover_for_read_file,
    recover_for_search_replace,
)


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Tiny project + populated GraphRAG index, scoped to one test."""
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / "parser").mkdir()
    (proj / "parser" / "__init__.py").write_text("")
    (proj / "parser" / "lexer.py").write_text(textwrap.dedent('''
        """Lexer for the language."""

        def lex(source):
            """Tokenize source into a list of tokens."""
            return []

        class Lexer:
            """Stateful lexer."""
            pass
    '''))
    (proj / "docs").mkdir()
    (proj / "docs" / "design.md").write_text(textwrap.dedent("""
        # Lexer design

        The lexer module exports `lex(source)` and the `Lexer` class.
    """).strip())

    monkeypatch.chdir(proj)
    # Pre-build the index so context_recovery doesn't have to.
    db_path = proj / ".drydock" / "graphrag.sqlite"
    db_path.parent.mkdir()
    monkeypatch.setenv("DRYDOCK_GRAPHRAG_DB", str(db_path))
    from drydock.graphrag import Index
    Index(db_path).ingest_path(proj)
    return proj


def test_recover_for_search_replace_finds_symbol_in_other_file(project: Path):
    """Agent searched in utils/parse.py but `def lex(` lives in parser/lexer.py."""
    recovery = recover_for_search_replace(
        file_path="utils/parse.py",
        search_text="def lex(source):",
    )
    assert recovery is not None
    assert "lex" in recovery.lower()
    # The result formatted block should mention the actual file
    assert "lexer.py" in recovery


def test_recover_for_read_file_finds_misplaced_path(project: Path):
    recovery = recover_for_read_file("utils/lexer.py")
    assert recovery is not None
    assert "lexer.py" in recovery
    # Found the right file location
    assert "parser" in recovery


def test_recover_for_grep_falls_back_to_word_query(project: Path):
    recovery = recover_for_grep(
        pattern=r"def\s+lex\(",
        exit_code=1,
        stdout="",
    )
    assert recovery is not None
    assert "lex" in recovery.lower()


def test_recover_for_grep_skips_when_grep_succeeded(project: Path):
    """No recovery if grep actually returned matches."""
    recovery = recover_for_grep(
        pattern="def lex",
        exit_code=0,
        stdout="parser/lexer.py:5:def lex(source):",
    )
    assert recovery is None


def test_recover_returns_none_for_nonexistent_query(project: Path):
    """Query that doesn't match anything → no recovery, not an error."""
    recovery = recover_for_query("zzzzz_no_such_thing_anywhere")
    assert recovery is None


def test_recover_returns_none_with_empty_search_text(project: Path):
    """No symbol to extract → no recovery."""
    recovery = recover_for_search_replace(
        file_path="",
        search_text="",
    )
    assert recovery is None


def test_recover_skips_uninteresting_basenames(project: Path):
    """Don't try to retrieve for stems like __init__ or main."""
    recovery = recover_for_read_file("foo/__init__.py")
    assert recovery is None


def test_recover_no_index_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """No GraphRAG index → recovery returns None silently, never raises."""
    monkeypatch.setenv("DRYDOCK_GRAPHRAG_DB", str(tmp_path / "no.sqlite"))
    monkeypatch.chdir(tmp_path)  # no project markers
    recovery = recover_for_query("anything")
    assert recovery is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

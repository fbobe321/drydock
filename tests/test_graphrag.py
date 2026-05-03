"""End-to-end tests for the GraphRAG first cut.

Each test ingests a small synthetic project into a tmp SQLite and
verifies the retriever returns sensible hits. We exercise the failure
shapes from TRIAGE_v1.md:

- Pattern 4 (cross-package inheritance) — parent-class chain walking
- Pattern 10c (multi-module memory) — markdown chunk retrieval
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from drydock.graphrag.code_indexer import index_path as index_code
from drydock.graphrag.storage import Index
from drydock.graphrag.text_indexer import index_path as index_text


@pytest.fixture()
def tiny_project(tmp_path: Path) -> Path:
    """A synthetic project with a class hierarchy across 'packages' plus
    a markdown design doc, exercising both retrieval shapes."""
    werkzeug = tmp_path / "werkzeug"
    werkzeug.mkdir()
    (werkzeug / "__init__.py").write_text("")
    (werkzeug / "wrappers.py").write_text(textwrap.dedent('''
        """Werkzeug wrappers — base Request class with is_json."""

        class Request:
            """Parent Request; defines is_json."""

            @property
            def is_json(self):
                """True if content-type is application/json."""
                return False

            def get_json(self):
                return None
    '''))

    flask = tmp_path / "flask"
    flask.mkdir()
    (flask / "__init__.py").write_text("")
    (flask / "wrappers.py").write_text(textwrap.dedent('''
        """Flask wrappers — extend werkzeug Request."""
        from werkzeug.wrappers import Request as WerkzeugRequest


        class Request(WerkzeugRequest):
            """Flask's Request subclass."""

            def get_json(self, silent: bool = False):
                """Override to support silent mode."""
                return super().get_json()
    '''))

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "design.md").write_text(textwrap.dedent("""
        # Project Design

        ## Caching strategy

        We invalidate the cache on every write because read-mostly
        workloads dominate. Eventual consistency is acceptable.

        ## Authentication

        Sessions use HTTP-only cookies signed with HMAC-SHA256.
        The signing key rotates daily.

        ## Logging

        Structured JSON logs go to stderr. PII is masked at the
        sink, not at the logger.
    """).strip())

    return tmp_path


def test_code_indexer_walks_synthetic_project(tiny_project: Path):
    records = list(index_code(tiny_project))
    by_qualname = {r.qualname: r for r in records}

    # Both Request classes indexed under their package qualnames.
    werkzeug_request = by_qualname.get("werkzeug.wrappers.Request")
    flask_request = by_qualname.get("flask.wrappers.Request")
    assert werkzeug_request is not None
    assert flask_request is not None

    # Flask Request records werkzeug as parent.
    assert any("WerkzeugRequest" in p or "Request" in p for p in flask_request.parents)

    # Method on werkzeug Request is recorded as a method (not function).
    is_json = next(
        (r for r in records if r.name == "is_json"),
        None,
    )
    assert is_json is not None
    assert is_json.kind == "method"


def test_text_indexer_chunks_markdown_by_section(tiny_project: Path):
    chunks = list(index_text(tiny_project))
    assert len(chunks) >= 3   # at least: caching, auth, logging
    seen_sections = {c.content.splitlines()[0] for c in chunks if c.content}
    assert any("Caching strategy" in s for s in seen_sections)
    assert any("Authentication" in s for s in seen_sections)


def test_index_retrieves_text_by_query(tiny_project: Path, tmp_path: Path):
    db = tmp_path / "g.sqlite"
    idx = Index(db)
    counts = idx.ingest_path(tiny_project)
    assert counts["symbols"] >= 4
    assert counts["chunks"] >= 3

    result = idx.retrieve("how does the cache invalidate")
    assert result.text, "expected at least one text hit"
    top = result.text[0]
    assert "cache" in top.content.lower()
    assert top.citation_id.endswith(":1-5") or "design.md" in top.citation_id


def test_index_finds_symbol_across_packages(tiny_project: Path, tmp_path: Path):
    db = tmp_path / "g.sqlite"
    idx = Index(db)
    idx.ingest_path(tiny_project)

    hits = idx.find_symbol("Request")
    qualnames = {h.qualname for h in hits}
    assert "werkzeug.wrappers.Request" in qualnames
    assert "flask.wrappers.Request" in qualnames


def test_index_walks_inheritance_chain(tiny_project: Path, tmp_path: Path):
    """Pattern 4 mitigation: model can find that flask.Request inherits
    from werkzeug's Request without reading all the children files."""
    db = tmp_path / "g.sqlite"
    idx = Index(db)
    idx.ingest_path(tiny_project)

    chain = idx.inheritance_chain("flask.wrappers.Request")
    assert len(chain) >= 2
    assert chain[0].qualname == "flask.wrappers.Request"
    # Second link should be the werkzeug parent (resolved via name match).
    assert chain[1].qualname == "werkzeug.wrappers.Request"


def test_index_is_idempotent(tiny_project: Path, tmp_path: Path):
    db = tmp_path / "g.sqlite"
    idx = Index(db)
    idx.ingest_path(tiny_project)
    s1 = idx.stats()
    # Re-ingest the same project; counts should not double.
    idx.ingest_path(tiny_project)
    s2 = idx.stats()
    assert s1 == s2


def test_index_refreshes_on_file_change(tiny_project: Path, tmp_path: Path):
    db = tmp_path / "g.sqlite"
    idx = Index(db)
    idx.ingest_path(tiny_project)
    s1 = idx.stats()
    # Add a new symbol in an existing file — re-ingest must pick it up.
    (tiny_project / "flask" / "wrappers.py").write_text(textwrap.dedent('''
        """Flask wrappers — extended."""
        from werkzeug.wrappers import Request as WerkzeugRequest


        class Request(WerkzeugRequest):
            """Flask's Request."""

            def get_json(self, silent: bool = False):
                return super().get_json()


        class Response:
            """New class added on second ingest."""
            pass
    '''))
    idx.ingest_path(tiny_project)
    s2 = idx.stats()
    assert s2["symbols"] > s1["symbols"]
    # The new class is findable.
    assert any(h.name == "Response" for h in idx.find_symbol("Response"))


def test_retrieve_handles_empty_query(tmp_path: Path):
    db = tmp_path / "g.sqlite"
    idx = Index(db)
    result = idx.retrieve("")
    assert result.is_empty()


def test_retrieve_against_empty_index(tmp_path: Path):
    db = tmp_path / "g.sqlite"
    idx = Index(db)
    result = idx.retrieve("anything")
    assert result.is_empty()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

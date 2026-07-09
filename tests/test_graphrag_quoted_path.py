"""A user wrapping the path in quotes — common on Windows, e.g.
`/graphrag build "C:\\Users\\me\\Documents"` — must still index. Before the fix
the quote chars became part of the path, isabs() was False, it was joined under
cwd, and nothing was found ('No text found. Nothing was indexed.')."""
from __future__ import annotations

import os
import tempfile

from drydock import graphrag


def _make_docs():
    src = tempfile.mkdtemp()
    open(os.path.join(src, "notes.txt"), "w").write("Apollo landed on the Moon in 1969.")
    open(os.path.join(src, "readme.md"), "w").write("# Project\nDrydock is a coding agent.")
    store = os.path.join(tempfile.mkdtemp(), "kb.json")  # store OUTSIDE the scanned dir
    return src, store


def test_double_quoted_path_indexes():
    src, store = _make_docs()
    stats = graphrag.build_index([f'"{src}"'], store)
    assert stats["files"] == 2 and stats["chunks"] >= 2


def test_single_quoted_path_indexes():
    src, store = _make_docs()
    stats = graphrag.build_index([f"'{src}'"], store)
    assert stats["files"] == 2


def test_unquoted_path_still_works():
    src, store = _make_docs()
    assert graphrag.build_index([src], store)["files"] == 2


def test_unquote_helper():
    assert graphrag._unquote('"C:\\Users\\x"') == "C:\\Users\\x"
    assert graphrag._unquote("'/a/b'") == "/a/b"
    assert graphrag._unquote("/plain/path") == "/plain/path"
    assert graphrag._unquote('  "spaced"  ') == "spaced"
    assert graphrag._unquote('"') == '"'  # lone quote unchanged


def test_one_bad_file_does_not_abort_build():
    """A single file that raises mid-processing must be skipped, not crash the
    whole folder build (the Windows 'NoneType.strip' report)."""
    from unittest import mock
    src, store = _make_docs()
    open(os.path.join(src, "boom.txt"), "w").write("EXPLODE here")
    real = graphrag.extract_entities

    def boom(text):
        if "EXPLODE" in text:
            raise AttributeError("'NoneType' object has no attribute 'strip'")
        return real(text)

    with mock.patch.object(graphrag, "extract_entities", boom):
        stats = graphrag.build_index([src], store)
    assert stats["files"] == 2  # the 2 good files indexed, boom.txt skipped (not counted)


def test_build_knowledge_tool_builds_and_reports():
    from drydock import tools
    src, store = _make_docs()
    out = tools.tool_build_knowledge({"path": f'"{src}"', "mode": "build"},
                                     {"cwd": os.getcwd(), "graphrag_store": store})
    assert "Built the knowledge base" in out and "2 files" in out


def test_build_knowledge_available_to_gemma():
    from drydock import tool_registry
    from drydock.tuning import filter_tool_schemas
    names = [s["name"] for s in filter_tool_schemas(tool_registry.schemas(), "gemma4")]
    assert "BuildKnowledge" in names

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

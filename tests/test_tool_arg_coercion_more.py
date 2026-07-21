"""Wrong-type args crashed more tools with AttributeError ('.strip()' on a
list/int): GitCommit message, Knowledge query, Consult question, WebFetch url,
WebSearch query. All coerced now — no tool should raise on a wrong-type arg."""
from __future__ import annotations

import drydock.tools as T


def _no_crash(fn, **kw):
    out = fn(kw, {"cwd": "/tmp"})   # must return a string, not raise
    assert isinstance(out, str)
    return out


def test_gitcommit_message_list():
    _no_crash(T.tool_gitcommit, message=["line1", "line2"])


def test_gitcommit_message_int():
    _no_crash(T.tool_gitcommit, message=42)


def test_knowledge_query_list():
    _no_crash(T.tool_knowledge, query=["find x"])


def test_consult_question_list():
    _no_crash(T.tool_consult, question=["help me"])


def test_webfetch_url_list():
    _no_crash(T.tool_webfetch, url=["http://example.invalid"])


def test_websearch_query_int():
    out = _no_crash(T.tool_websearch, query=42)
    assert "42" in out


def test_gitdiff_path_list():
    # path as a single-element list must unwrap, not TypeError in gittools.diff.
    _no_crash(T.tool_gitdiff, path=["some/file.py"])


def test_viewimage_path_list():
    # path as a list must unwrap, not AttributeError on .strip().
    out = _no_crash(T.tool_viewimage, path=["/no/such.png"])
    assert "such.png" in out  # names the (unwrapped) path in its error

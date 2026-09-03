"""Regression: the Knowledge tool must be usable when a knowledge base EXISTS.

Bug (2026-09-03): dynamic tool selection gated Knowledge behind the keywords
knowledge/graph/entity/graphrag/ingest. A GraphRAG user asking a plain question
("what does the auth module do?") types none of those, so with 46 tools and a cap
of 12 the tool was trimmed and the model's call was rejected with
"[The 'Knowledge' tool is not available here]" — contradicting the product's own
help text, which promises retrieval "when you just ASK a question".
"""
import sqlite3
import tempfile

import drydock.tools as T
from drydock.graphrag import default_store_path, knowledge_base_exists
from drydock.tool_select import select_tools

SCHEMAS = getattr(T, "SCHEMAS", None) or getattr(T, "TOOL_SCHEMAS")
PLAIN_QUESTION = "what does the authentication module do?"


def _toolset(cwd, task=PLAIN_QUESTION):
    pins = ["Knowledge"] if knowledge_base_exists(cwd) else []
    return [s["name"] for s in select_tools(
        SCHEMAS, phase="understand", task_text=task, max_tools=12, pin_tools=pins)]


def test_kb_exists_makes_knowledge_available_for_a_plain_question():
    d = tempfile.mkdtemp()
    p = default_store_path(d)
    p.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(str(p)).close()
    assert knowledge_base_exists(d)
    assert "Knowledge" in _toolset(d), \
        "a built KB must make Knowledge reachable without saying the word 'knowledge'"


def test_no_kb_leaves_knowledge_gated():
    # Nothing to search: the tool stays behind the keyword gate so the slot is
    # spent on something useful.
    d = tempfile.mkdtemp()
    assert not knowledge_base_exists(d)
    assert "Knowledge" not in _toolset(d)


def test_core_tools_still_present_when_knowledge_is_pinned():
    d = tempfile.mkdtemp()
    p = default_store_path(d)
    p.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(str(p)).close()
    sel = _toolset(d)
    for core in ("Read", "Write", "Edit", "Bash", "Glob", "Grep", "todo"):
        assert core in sel, f"pinning Knowledge must not displace {core}"


def test_existence_check_never_raises_on_a_bad_path():
    assert knowledge_base_exists("\x00 not a path") is False

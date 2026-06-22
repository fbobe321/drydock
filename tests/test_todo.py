"""Tests for the `todo` checklist tool and its forgiving parser."""
from __future__ import annotations

from drydock.tools import parse_todo, tool_todo


def test_parse_todo_basic_statuses():
    items = parse_todo("[x] done one\n[~] doing two\n[ ] pending three")
    assert items == [
        ("done one", "done"),
        ("doing two", "in_progress"),
        ("pending three", "pending"),
    ]


def test_parse_todo_accepts_list_input():
    # The model sometimes sends a JSON array instead of one newline string —
    # this must NOT crash (it took down the whole TUI before).
    items = parse_todo([
        "[~] Explore existing tests and run them",
        "[ ] Add edge-case tests",
        "[ ] Verify all tests pass",
    ])
    assert [s for _, s in items] == ["in_progress", "pending", "pending"]
    assert items[0][0] == "Explore existing tests and run them"


def test_parse_todo_none_and_empty():
    assert parse_todo(None) == []
    assert parse_todo("") == []
    assert parse_todo([]) == []


def test_tool_todo_accepts_list(tmp_path):
    cfg: dict = {}
    out = tool_todo({"tasks": ["[ ] a", "[x] b"]}, cfg)
    assert "2 tasks" in out and "1 done" in out
    assert len(cfg["_todo"]) == 2

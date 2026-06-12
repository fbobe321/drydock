"""Tests for the Write/Edit undo journal."""
from __future__ import annotations

from drydock.tools import tool_write, tool_edit, undo_last


def test_undo_removes_a_newly_created_file(tmp_path):
    cfg = {"cwd": str(tmp_path)}
    tool_write({"file_path": "new.txt", "content": "hello"}, cfg)
    assert (tmp_path / "new.txt").exists()
    msg = undo_last(cfg)
    assert "removed" in msg
    assert not (tmp_path / "new.txt").exists()


def test_undo_restores_overwritten_content(tmp_path):
    cfg = {"cwd": str(tmp_path)}
    target = tmp_path / "f.txt"
    target.write_text("original")
    tool_write({"file_path": "f.txt", "content": "replaced"}, cfg)
    assert target.read_text() == "replaced"
    undo_last(cfg)
    assert target.read_text() == "original"


def test_undo_restores_an_edit(tmp_path):
    cfg = {"cwd": str(tmp_path)}
    target = tmp_path / "code.py"
    target.write_text("x = 1\n")
    tool_edit({"file_path": "code.py", "old_string": "x = 1", "new_string": "x = 2"}, cfg)
    assert "x = 2" in target.read_text()
    undo_last(cfg)
    assert target.read_text() == "x = 1\n"


def test_undo_is_lifo_across_multiple_writes(tmp_path):
    cfg = {"cwd": str(tmp_path)}
    tool_write({"file_path": "a.txt", "content": "A"}, cfg)
    tool_write({"file_path": "b.txt", "content": "B"}, cfg)
    undo_last(cfg)  # undoes b
    assert not (tmp_path / "b.txt").exists()
    assert (tmp_path / "a.txt").exists()


def test_undo_with_empty_journal(tmp_path):
    assert undo_last({"cwd": str(tmp_path)}) == "Nothing to undo."

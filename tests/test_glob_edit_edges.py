"""More edge cases from probing: Glob must not crash on a missing/list pattern,
and an empty Edit old_string should say what's wrong (not the baffling generic
'found N times')."""
from __future__ import annotations

from drydock.tools import tool_glob, tool_edit


def test_glob_missing_pattern_is_error_not_crash():
    out = tool_glob({}, {"cwd": "/tmp"})
    assert out.startswith("Error:") and "pattern" in out


def test_glob_list_pattern_unwrapped(tmp_path):
    (tmp_path / "a.py").write_text("x")
    out = tool_glob({"pattern": ["*.py"], "path": str(tmp_path)}, {"cwd": str(tmp_path)})
    assert "a.py" in out


def test_glob_no_matches(tmp_path):
    out = tool_glob({"pattern": "*.nope", "path": str(tmp_path)}, {"cwd": str(tmp_path)})
    assert out.strip() == "(no matches)"


def test_edit_empty_old_string_clear_error(tmp_path):
    f = tmp_path / "e.txt"; f.write_text("hello\n")
    out = tool_edit({"file_path": str(f), "old_string": "", "new_string": "X"}, {"cwd": str(tmp_path)})
    assert "empty" in out.lower() and f.read_text() == "hello\n"

"""Model-supplied tool args that arrive as the wrong type (a JSON array of lines,
a number, a stringified int, a single-element-list-wrapped path) must be coerced,
never crash — tools return results, never raise. Systematic across the file tools."""
from __future__ import annotations

from drydock.tools import (
    _as_text, _as_str_arg, _coerce_int,
    tool_edit, tool_read, tool_grep, tool_write,
)


def test_helpers():
    assert _as_text(["a", "b"]) == "a\nb"
    assert _as_text(None) == "" and _as_text(42) == "42" and _as_text("x") == "x"
    assert _as_str_arg(["/p"]) == "/p" and _as_str_arg(None) == "" and _as_str_arg(5) == "5"
    assert _coerce_int("5", 0) == 5 and _coerce_int("junk", 7) == 7 and _coerce_int(None, 3) == 3


def _cfg(tmp): return {"cwd": str(tmp)}


def test_edit_non_string_new(tmp_path):
    f = tmp_path / "e.txt"; f.write_text("alpha beta\n")
    out = tool_edit({"file_path": str(f), "old_string": "alpha", "new_string": ["x", "y"]}, _cfg(tmp_path))
    assert "Error" not in out and f.read_text().startswith("x\ny")


def test_edit_int_new(tmp_path):
    f = tmp_path / "e.txt"; f.write_text("beta\n")
    out = tool_edit({"file_path": str(f), "old_string": "beta", "new_string": 99}, _cfg(tmp_path))
    assert "99" in f.read_text() and "Error" not in out


def test_read_list_path_and_str_offsets(tmp_path):
    f = tmp_path / "r.txt"; f.write_text("line1\nline2\n")
    out = tool_read({"file_path": [str(f)], "offset": "0", "limit": "5"}, _cfg(tmp_path))
    assert "line1" in out


def test_grep_list_pattern(tmp_path):
    f = tmp_path / "g.txt"; f.write_text("needle here\n")
    out = tool_grep({"pattern": ["needle"], "path": str(f)}, _cfg(tmp_path))
    assert "needle" in out


def test_write_list_content(tmp_path):
    f = tmp_path / "w.txt"
    tool_write({"file_path": str(f), "content": ["a", "b"]}, _cfg(tmp_path))
    assert f.read_text() == "a\nb"

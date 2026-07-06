"""tool_write must not raise on non-string content. Local models sometimes send
`content` as a JSON array of lines (or a number); f.write() would TypeError, and
tools must return a result, never raise. A list → newline-joined lines."""
from __future__ import annotations

from drydock.tools import tool_write


def _write(tmp_path, content):
    fp = tmp_path / "out.txt"
    out = tool_write({"file_path": str(fp), "content": content}, {"cwd": str(tmp_path)})
    return out, fp.read_text()


def test_list_content_joined(tmp_path):
    out, text = _write(tmp_path, ["line1", "line2", "line3"])
    assert "Error" not in out and text == "line1\nline2\nline3"


def test_int_content_stringified(tmp_path):
    out, text = _write(tmp_path, 42)
    assert "Error" not in out and text == "42"


def test_none_content_empty(tmp_path):
    out, text = _write(tmp_path, None)
    assert "Error" not in out and text == ""


def test_normal_string_unchanged(tmp_path):
    out, text = _write(tmp_path, "hello\nworld")
    assert text == "hello\nworld"


def test_list_of_numbers(tmp_path):
    out, text = _write(tmp_path, [1, 2, 3])
    assert text == "1\n2\n3"

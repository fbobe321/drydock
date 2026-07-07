"""The Edit tool honors replace_all (models expect it, like the standard Edit
tool). Without it, multiple matches are an error; with it, every occurrence is
replaced. Found by probing Edit edge cases."""
from __future__ import annotations

from drydock.tools import tool_edit
from drydock import tool_registry


def _edit(tmp_path, setup, **kw):
    f = tmp_path / "f.txt"; f.write_text(setup)
    out = tool_edit({"file_path": str(f), **kw}, {"cwd": str(tmp_path)})
    return out, f.read_text()


def test_replace_all_replaces_every_occurrence(tmp_path):
    out, text = _edit(tmp_path, "a a a\n", old_string="a", new_string="b", replace_all=True)
    assert "Error" not in out and text == "b b b\n" and "3 occurrences" in out


def test_default_still_errors_on_multiple(tmp_path):
    out, text = _edit(tmp_path, "a a a\n", old_string="a", new_string="b")
    assert "found 3 times" in out and text == "a a a\n"  # unchanged


def test_replace_all_single_occurrence(tmp_path):
    out, text = _edit(tmp_path, "just one\n", old_string="one", new_string="two", replace_all=True)
    assert text == "just two\n"


def test_replace_all_no_match_errors(tmp_path):
    out, text = _edit(tmp_path, "hello\n", old_string="zzz", new_string="b", replace_all=True)
    assert "not found" in out and text == "hello\n"


def test_replace_all_in_schema():
    assert "replace_all" in tool_registry.get("Edit").schema["input_schema"]["properties"]

"""Tests for advisory write/edit guards and tool hardening."""
from __future__ import annotations

from pathlib import Path

from drydock.guards import (
    main_entry_warning,
    python_syntax_warning,
    stub_warning,
    write_warnings,
)
from drydock.tools import tool_edit, tool_write


# ── guards ────────────────────────────────────────────────────────────────

def test_syntax_warning_flags_broken_python():
    w = python_syntax_warning("x.py", "def f(:\n    pass")
    assert w and "syntax error" in w


def test_syntax_warning_clean_python():
    assert python_syntax_warning("x.py", "def f():\n    return 1\n") is None


def test_syntax_warning_ignores_non_python():
    assert python_syntax_warning("x.txt", "def f(:") is None


def test_main_entry_warning_undefined_call():
    src = "if __name__ == '__main__':\n    main()\n"
    w = main_entry_warning("x.py", src)
    assert w and "main" in w


def test_main_entry_warning_defined_ok():
    src = "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n"
    assert main_entry_warning("x.py", src) is None


def test_stub_warning_all_stubs():
    src = "def a():\n    pass\n\ndef b():\n    ...\n"
    assert stub_warning("x.py", src) is not None


def test_stub_warning_real_impl_ok():
    src = "def a():\n    return 1\n\ndef b():\n    return 2\n"
    assert stub_warning("x.py", src) is None


def test_write_warnings_syntax_short_circuits():
    ws = write_warnings("x.py", "def f(:\n pass")
    assert len(ws) == 1 and "syntax" in ws[0]


# ── tool_write ────────────────────────────────────────────────────────────

def test_tool_write_appends_syntax_warning(tmp_path):
    fp = str(tmp_path / "bad.py")
    out = tool_write({"file_path": fp, "content": "def f(:\n pass"}, {})
    assert "Wrote" in out and "syntax error" in out
    assert Path(fp).exists()  # write still happened (advisory, not blocking)


def test_tool_write_clean_no_warning(tmp_path):
    fp = str(tmp_path / "ok.py")
    out = tool_write({"file_path": fp, "content": "x = 1\n"}, {})
    assert "Wrote" in out and "WARNING" not in out


def test_tool_write_missing_path():
    assert "Error" in tool_write({"content": "x"}, {})


# ── tool_edit ─────────────────────────────────────────────────────────────

def test_tool_edit_already_applied_is_noop(tmp_path):
    fp = tmp_path / "f.py"
    fp.write_text("y = 2\n")
    out = tool_edit(
        {"file_path": str(fp), "old_string": "y = 1", "new_string": "y = 2"}, {}
    )
    assert "already" in out.lower()


def test_tool_edit_missing_path():
    assert "Error" in tool_edit({"old_string": "a", "new_string": "b"}, {})


def test_tool_edit_not_found_guidance(tmp_path):
    fp = tmp_path / "f.py"
    fp.write_text("a = 1\n")
    out = tool_edit(
        {"file_path": str(fp), "old_string": "zzz", "new_string": "b"}, {}
    )
    assert "not found" in out and "Read the file" in out


def test_tool_edit_post_edit_syntax_warning(tmp_path):
    fp = tmp_path / "f.py"
    fp.write_text("def f():\n    return 1\n")
    out = tool_edit(
        {"file_path": str(fp), "old_string": "return 1", "new_string": "return ("}, {}
    )
    assert "Edited" in out and "syntax error" in out


# ── cwd consistency (file tools agree with Bash) ──────────────────────────

def test_file_tools_honor_config_cwd(tmp_path):
    from drydock.tools import tool_read, tool_bash
    cfg = {"cwd": str(tmp_path)}
    # Write a relative path; it must land in cwd, where Bash can see it.
    tool_write({"file_path": "sub/x.py", "content": "v = 5\n"}, cfg)
    assert (tmp_path / "sub" / "x.py").exists()
    # Read with the same relative path resolves to the same file.
    assert "v = 5" in tool_read({"file_path": "sub/x.py"}, cfg)
    # Bash (which uses cwd) sees it too.
    assert "x.py" in tool_bash({"command": "ls sub"}, cfg)


def test_absolute_paths_pass_through(tmp_path):
    fp = str(tmp_path / "abs.py")
    tool_write({"file_path": fp, "content": "a=1\n"}, {"cwd": "/nonexistent"})
    assert (tmp_path / "abs.py").exists()

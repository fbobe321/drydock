"""Tests for advisory write/edit guards and tool hardening."""
from __future__ import annotations

from pathlib import Path

from drydock.guards import (
    bare_raise_warning,
    main_entry_warning,
    python_syntax_warning,
    sibling_imports_warning,
    stub_warning,
    write_warnings,
)


def test_bare_raise_outside_except_warns():
    src = "def f(found):\n    if not found:\n        raise\n"
    w = bare_raise_warning("x.py", src)
    assert w and "bare `raise`" in w


def test_bare_raise_inside_except_is_ok():
    src = "try:\n    f()\nexcept Exception:\n    raise\n"
    assert bare_raise_warning("x.py", src) is None


def test_raise_with_exception_is_ok():
    src = "def f(found):\n    if not found:\n        raise ValueError('x')\n"
    assert bare_raise_warning("x.py", src) is None
from drydock.tools import tool_edit, tool_write


# ── sibling-imports guard (ported from v2) ──────────────────────────────────

def test_sibling_imports_warns_on_missing_relative_module(tmp_path):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    init = pkg / "__init__.py"
    w = sibling_imports_warning(str(init), "from .cli import CLI\n")
    assert w and "cli" in w and "don't exist" in w


def test_sibling_imports_quiet_when_module_exists(tmp_path):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "cli.py").write_text("class CLI: ...\n")
    init = pkg / "__init__.py"
    assert sibling_imports_warning(str(init), "from .cli import CLI\n") is None


def test_sibling_imports_quiet_for_subpackage(tmp_path):
    pkg = tmp_path / "mypkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "sub" / "__init__.py").write_text("")
    init = pkg / "__init__.py"
    assert sibling_imports_warning(str(init), "from .sub import x\n") is None


def test_sibling_imports_same_package_absolute(tmp_path):
    pkg = tmp_path / "geom"
    pkg.mkdir()
    init = pkg / "__init__.py"
    w = sibling_imports_warning(str(init), "from geom.shapes import area\n")
    assert w and "shapes" in w


def test_sibling_imports_ignores_stdlib_and_thirdparty(tmp_path):
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    init = pkg / "__init__.py"
    src = "import os\nimport sys\nfrom collections import OrderedDict\nimport requests\n"
    assert sibling_imports_warning(str(init), src) is None


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


def test_tool_write_blank_path_rejected(tmp_path):
    cfg = {"cwd": str(tmp_path)}
    for bad in ["", "   ", "\t", "\n"]:
        out = tool_write({"file_path": bad, "content": "x"}, cfg)
        assert out.startswith("Error") and "file_path" in out
    # No stray file got created from a blank path.
    assert list(tmp_path.iterdir()) == []


def test_tool_write_directory_path_rejected(tmp_path):
    sub = tmp_path / "adir"
    sub.mkdir()
    out = tool_write({"file_path": str(sub), "content": "x"}, {})
    assert out.startswith("Error") and "directory" in out


def test_tool_edit_blank_path_rejected():
    assert tool_edit({"file_path": "   ", "old_string": "a", "new_string": "b"}, {}).startswith("Error")


def test_conflict_markers_detected_and_refused(tmp_path):
    from drydock.guards import has_conflict_markers

    assert has_conflict_markers("<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE\n")
    assert has_conflict_markers("ok\n>>>>>>> REPLACE\n")
    # Decorative `=======` dividers must NOT trip it (common in real code).
    assert not has_conflict_markers("# =======================\nx = 1\n")
    assert not has_conflict_markers("def f():\n    return 1\n")


def test_tool_write_refuses_conflict_marker_content(tmp_path):
    fp = str(tmp_path / "c.py")
    out = tool_write(
        {"file_path": fp, "content": "<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\n"},
        {},
    )
    assert out.startswith("REFUSED") and "conflict markers" in out
    assert not Path(fp).exists()  # the corrupt write did not happen


def test_tool_edit_refuses_conflict_marker_new_string(tmp_path):
    fp = tmp_path / "e.py"
    fp.write_text("x = 1\n")
    out = tool_edit(
        {"file_path": str(fp), "old_string": "x = 1", "new_string": ">>>>>>> REPLACE"},
        {},
    )
    assert out.startswith("REFUSED")
    assert fp.read_text() == "x = 1\n"  # unchanged


def test_tool_write_appends_bare_raise_warning(tmp_path):
    fp = str(tmp_path / "br.py")
    out = tool_write(
        {"file_path": fp, "content": "def f(x):\n    if not x:\n        raise\n"}, {}
    )
    assert "Wrote" in out and "bare `raise`" in out


def test_tool_write_appends_sibling_imports_warning(tmp_path):
    pkg = tmp_path / "proj"
    pkg.mkdir()
    out = tool_write(
        {"file_path": str(pkg / "__init__.py"), "content": "from .core import run\n"},
        {},
    )
    assert "Wrote" in out and "core" in out and "don't exist" in out


# ── tool_edit ─────────────────────────────────────────────────────────────

def test_tool_edit_already_applied_is_noop(tmp_path):
    fp = tmp_path / "f.py"
    fp.write_text("y = 2\n")
    out = tool_edit(
        {"file_path": str(fp), "old_string": "y = 1", "new_string": "y = 2"}, {}
    )
    assert "already" in out.lower()


def test_tool_edit_infers_file_from_directory(tmp_path):
    (tmp_path / "a.py").write_text("print('hello')\n")
    (tmp_path / "target.py").write_text("UNIQUE_MARKER_LINE = 123\n")
    # Pass the DIRECTORY as file_path; old_string only lives in target.py.
    out = tool_edit(
        {"file_path": str(tmp_path), "old_string": "UNIQUE_MARKER_LINE = 123",
         "new_string": "UNIQUE_MARKER_LINE = 456"},
        {},
    )
    assert "Edited" in out
    assert (tmp_path / "target.py").read_text() == "UNIQUE_MARKER_LINE = 456\n"


def test_tool_edit_directory_ambiguous_errors(tmp_path):
    (tmp_path / "a.py").write_text("shared_text_here_long\n")
    (tmp_path / "b.py").write_text("shared_text_here_long\n")
    out = tool_edit(
        {"file_path": str(tmp_path), "old_string": "shared_text_here_long",
         "new_string": "x"},
        {},
    )
    assert "Error" in out and "directory" in out


def test_tool_edit_missing_path():
    assert "Error" in tool_edit({"old_string": "a", "new_string": "b"}, {})


def test_tool_edit_not_found_guidance(tmp_path):
    fp = tmp_path / "f.py"
    fp.write_text("a = 1\n")
    out = tool_edit(
        {"file_path": str(fp), "old_string": "zzz", "new_string": "b"}, {}
    )
    assert "not found" in out and "Read the file" in out


def test_tool_edit_fuzzy_applies_unique_near_miss(tmp_path):
    # A weak model often can't reproduce a block verbatim. A high-confidence,
    # unique, same-indent near-miss should APPLY (break the edit-loop) rather
    # than error: here old_string drops the spaces in "(a, b, c)".
    fp = tmp_path / "f.py"
    fp.write_text('def f():\n    x = compute(a, b, c)\n    return x\n')
    out = tool_edit(
        {"file_path": str(fp),
         "old_string": "    x = compute(a,b,c)\n    return x",
         "new_string": "    x = compute2(a, b, c)\n    return x"}, {})
    assert "Edited" in out
    assert "compute2" in fp.read_text()


def test_tool_edit_noop_when_old_equals_new(tmp_path):
    # old_string == new_string changes nothing. Reporting "Edited" is a false
    # success that loops a weak model; it must say "No change" and not write.
    fp = tmp_path / "f.py"
    fp.write_text("a = 1\n")
    out = tool_edit(
        {"file_path": str(fp), "old_string": "a = 1", "new_string": "a = 1"}, {}
    )
    assert "No change" in out and "Edited" not in out
    assert "move on" in out.lower()
    assert fp.read_text() == "a = 1\n"  # untouched


def test_tool_edit_fuzzy_refuses_indent_mismatch(tmp_path):
    # If the near-miss differs in INDENT, applying the model's new_string would
    # corrupt the file — so it must error (and show the real text) instead.
    fp = tmp_path / "f.py"
    src = 'def f():\n    x = compute(a, b, c)\n    return x\n'
    fp.write_text(src)
    out = tool_edit(
        {"file_path": str(fp),
         "old_string": "x = compute(a,b,c)\n    return x",   # no leading indent
         "new_string": "x = compute2(a, b, c)\n    return x"}, {})
    assert "not found" in out
    assert fp.read_text() == src  # untouched


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


def test_tool_bash_timeout_suggests_larger_timeout():
    from drydock.tools import tool_bash
    out = tool_bash({"command": "sleep 2", "timeout": 1}, {})
    assert "timed out after 1s" in out
    # Must tell the model it can retry with a bigger timeout (so a legitimately
    # slow query/build doesn't dead-end — what derailed query-optimize).
    assert "timeout: 4" in out


def test_absolute_paths_pass_through(tmp_path):
    fp = str(tmp_path / "abs.py")
    tool_write({"file_path": fp, "content": "a=1\n"}, {"cwd": "/nonexistent"})
    assert (tmp_path / "abs.py").exists()

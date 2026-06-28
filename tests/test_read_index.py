"""Semantic chunking: Read returns a STRUCTURE INDEX for a huge file (no
explicit window) instead of dumping it, so it can't blow the context window."""
from __future__ import annotations

from drydock.tools import tool_read


def _big_py(tmp_path, n=2000):
    lines = []
    for i in range(n):
        if i % 400 == 0:
            lines.append(f"def func_{i}(x):")
        elif i % 400 == 1:
            lines.append("    return x")
        else:
            lines.append(f"    # comment {i}")
    (tmp_path / "big.py").write_text("\n".join(lines))
    return {"cwd": str(tmp_path)}


def test_huge_file_no_window_returns_index(tmp_path):
    cfg = _big_py(tmp_path)
    out = tool_read({"file_path": "big.py"}, cfg)
    assert "STRUCTURE INDEX" in out
    assert "def func_0" in out and "def func_400" in out
    assert "# comment" not in out  # comments are not anchors


def test_index_is_small_not_a_dump(tmp_path):
    cfg = _big_py(tmp_path)
    out = tool_read({"file_path": "big.py"}, cfg)
    assert len(out.splitlines()) < 50  # an index, not 2000 lines


def test_explicit_window_still_reads_slice(tmp_path):
    cfg = _big_py(tmp_path)
    out = tool_read({"file_path": "big.py", "offset": 0, "limit": 3}, cfg)
    assert "STRUCTURE INDEX" not in out
    assert out.startswith("1\tdef func_0")


def test_small_file_reads_fully(tmp_path):
    (tmp_path / "s.py").write_text("def a():\n    return 1\n")
    out = tool_read({"file_path": "s.py"}, {"cwd": str(tmp_path)})
    assert "STRUCTURE INDEX" not in out and "def a()" in out


def test_markdown_headers_indexed(tmp_path):
    body = "\n".join(["# Title"] + [f"para {i}" for i in range(2000)] + ["## Section B"])
    (tmp_path / "doc.md").write_text(body)
    out = tool_read({"file_path": "doc.md"}, {"cwd": str(tmp_path)})
    assert "STRUCTURE INDEX" in out and "# Title" in out and "## Section B" in out

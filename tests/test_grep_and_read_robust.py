"""tool_grep must not report a grep ERROR (invalid regex, unreadable path — grep
exit >=2) as "(no matches)"; and both grep and Read must be binary-safe (no crash,
no raw NUL). Found by probing the grep/read I/O paths."""
from __future__ import annotations

from drydock.tools import tool_grep, tool_read


def _grep(pattern, path):
    return tool_grep({"pattern": pattern, "path": path}, {"cwd": "/tmp"})


def test_grep_valid_match(tmp_path):
    f = tmp_path / "a.txt"; f.write_text("hello world\nfoo\n")
    assert "hello world" in _grep("hello", str(f))


def test_grep_genuine_no_match(tmp_path):
    f = tmp_path / "a.txt"; f.write_text("hello\n")
    assert _grep("zzznope", str(f)).strip() == "(no matches)"


def test_grep_invalid_regex_is_error_not_no_matches(tmp_path):
    f = tmp_path / "a.txt"; f.write_text("hello\n")
    out = _grep("[unclosed", str(f))
    assert out.startswith("Error:") and "no matches" not in out


def test_grep_missing_path_is_error(tmp_path):
    out = _grep("x", str(tmp_path / "nope_xyz"))
    assert out.startswith("Error:")


def test_grep_binary_does_not_crash(tmp_path):
    f = tmp_path / "b.bin"; f.write_bytes(b"\xff\xfe match \x00\xc0\n")
    out = _grep("match", str(f))
    assert "Error:" not in out or "no matches" in out or "match" in out  # no exception


def test_read_drops_nul_bytes(tmp_path):
    f = tmp_path / "n.txt"; f.write_bytes(b"before\x00\x00after text\n")
    out = tool_read({"file_path": str(f)}, {"cwd": str(tmp_path)})
    assert "\x00" not in out and "after text" in out

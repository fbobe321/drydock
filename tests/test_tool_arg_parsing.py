"""Tolerant parsing of a model's tool-call arguments.

Gemma builds Bash commands with heredocs (`cat <<EOF ...`) whose arguments
carry LITERAL newlines. Strict json.loads rejects those ("Invalid control
character"), the call degraded to {"_raw": ...} with no `command`, Bash failed,
and the model's retries nested {"_raw": "{\\"_raw\\": ...}"} into a fatal loop
(seen live on crack-7z-hash). _parse_tool_args fixes both."""
from __future__ import annotations

import json

from drydock.providers import _parse_tool_args
from drydock.tools import tool_bash


def test_heredoc_with_literal_newlines_recovers_command():
    raw = '{"command": "cat <<EOF > p.txt\npassword\n123456\nEOF"}'
    out = _parse_tool_args(raw)
    assert out.get("command", "").startswith("cat <<EOF > p.txt")
    assert "\n" in out["command"]  # newlines preserved


def test_nested_raw_is_unwrapped():
    triple = json.dumps({"_raw": json.dumps({"_raw": json.dumps({"command": "ls -la"})})})
    assert _parse_tool_args(triple) == {"command": "ls -la"}


def test_normal_and_empty_and_broken():
    assert _parse_tool_args('{"command":"echo hi"}') == {"command": "echo hi"}
    assert _parse_tool_args("") == {}
    assert _parse_tool_args("{not json") == {"_raw": "{not json"}


def test_bash_gives_recovery_hint_on_unparsed_args():
    out = tool_bash({"_raw": "garbage"}, {})
    assert "not valid JSON" in out and '{"command"' in out


def test_bash_runs_recovered_heredoc(tmp_path):
    # End-to-end: a heredoc command recovered by _parse_tool_args actually runs.
    raw = ('{"command": "cat <<EOF > note.txt\nhello\nworld\nEOF"}')
    params = _parse_tool_args(raw)
    out = tool_bash(params, {"cwd": str(tmp_path)})
    assert "Error" not in out
    assert (tmp_path / "note.txt").read_text() == "hello\nworld\n"


# ── unescaped double-quotes inside string values (quote-heavy content) ──────
# Seen live: gemma-4 thrashed ~1h writing an HTML file — every Write emitted
# {"content": "<img src="x" ...>", "file_path": ...} whose inner quotes broke
# strict AND non-strict json.loads, degrading to {"_raw"} so Write got no args.
# _repair_json_args recovers the fields.

def test_unescaped_quotes_in_html_content_recovers():
    raw = '{"content": "<img src="x" onerror=alert(1)>", "file_path": "/app/out.html"}'
    out = _parse_tool_args(raw)
    assert out.get("file_path") == "/app/out.html"
    assert out.get("content") == '<img src="x" onerror=alert(1)>'


def test_unescaped_quotes_with_newlines():
    raw = '{"file_path": "/a.py", "content": "def f():\n    print("hi")\n"}'
    out = _parse_tool_args(raw)
    assert out.get("file_path") == "/a.py"
    assert out.get("content") == 'def f():\n    print("hi")\n'


def test_repair_does_not_touch_wellformed():
    # Escaped quotes must decode once (no double-escaping); clean JSON is untouched.
    assert _parse_tool_args('{"content": "say \\"hi\\""}') == {"content": 'say "hi"'}
    assert _parse_tool_args('{"path": "/x", "overwrite": true}') == {
        "path": "/x", "overwrite": True}


def test_repair_recovered_write_has_usable_fields():
    # The recovered dict is the shape Write/Edit need (both keys present, no _raw).
    raw = '{"file_path": "/app/out.html", "content": "<a href="j">x</a>"}'
    out = _parse_tool_args(raw)
    assert "_raw" not in out
    assert set(out) == {"file_path", "content"}

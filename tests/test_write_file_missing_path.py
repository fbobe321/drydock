"""Regression test: write_file missing-path loop fix.

Stress run 2026-04-28 showed Gemma 4 calling write_file(content="...")
without a path. format.py returned "write_file: missing required `path`..."
and agent_loop.py wrapped it as "<tool_error>write_file: write_file: ..."
producing a confusing double-prefix. Also, the error message placeholder
"your/file.py" gave the model no real hint.

Fixes in format.py:
1. Remove tool-name prefix from error msg (agent_loop adds it already).
2. When content first line is "# filename.py", infer path and succeed.
3. When path can't be inferred, include project .py file listing in error.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from drydock.core.llm.format import APIToolFormatHandler, ParsedMessage, ParsedToolCall


def _make_tool_manager():
    tm = MagicMock()
    from drydock.core.tools.builtins.write_file import WriteFile
    tm.available_tools = {"write_file": WriteFile}
    return tm


class TestWriteFileMissingPath:
    def _resolve(self, raw_args: dict) -> object:
        handler = APIToolFormatHandler()
        parsed = ParsedMessage(
            tool_calls=[
                ParsedToolCall(
                    tool_name="write_file",
                    call_id="tc1",
                    raw_args=raw_args,
                )
            ],
            text_content="",
        )
        return handler.resolve_tool_calls(parsed, _make_tool_manager())

    def test_missing_path_no_double_prefix(self):
        """Error for missing path must NOT start with 'write_file:'."""
        result = self._resolve({"content": "import os\nprint('hello')\n"})
        assert result.failed_calls
        err = result.failed_calls[0].error
        # agent_loop.py will add "write_file: " prefix — the error itself must not
        assert not err.startswith("write_file:"), f"Double prefix found: {err!r}"

    def test_missing_path_error_has_useful_hint(self):
        """Error should mention path parameter and how to fix it."""
        result = self._resolve({"content": "import os\nprint('hello')\n"})
        err = result.failed_calls[0].error
        assert "path" in err.lower()
        # Must not contain the useless placeholder "your/file.py"
        assert "your/file.py" not in err

    def test_path_inferred_from_comment(self, tmp_path, monkeypatch):
        """When first line is '# mymodule.py', path is inferred and write succeeds."""
        monkeypatch.chdir(tmp_path)
        content = "# mymodule.py\nimport os\n\ndef hello():\n    pass\n"
        result = self._resolve({"content": content})
        # Should resolve (no failed call), path inferred as mymodule.py
        assert not result.failed_calls, f"Expected no failure, got: {result.failed_calls[0].error!r}"
        assert result.tool_calls
        assert result.tool_calls[0].validated_args.path == "mymodule.py"

    def test_path_not_inferred_from_plain_import(self):
        """Content starting with bare import (no comment) must produce a failed call."""
        result = self._resolve({"content": "import abc\nimport os\nimport datetime\n"})
        assert result.failed_calls
        assert not result.tool_calls

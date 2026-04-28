"""Regression test for fix: truncated-arg FailedToolCall hint includes file_path.

When _truncate_old_tool_results truncates a write_file call, it keeps the
file_path field in the stub (verified by test_truncate_args_valid_json.py).
But the old resolve_tool_calls path hint extractor only checked for 'path',
not 'file_path', so the Re-read advisory was empty for write_file/search_replace.

Fix: check file_path as fallback if path is absent.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from drydock.core.llm.format import APIToolFormatHandler, ParsedMessage, ParsedToolCall


def _make_tool_manager_with(tool_name: str, tool_class):
    tm = MagicMock()
    tm.available_tools = {tool_name: tool_class}
    return tm


def _make_write_file_class():
    """Minimal stand-in for WriteFile that satisfies resolve_tool_calls."""
    from drydock.core.tools.builtins.write_file import WriteFile
    return WriteFile


class TestTruncatedArgPathHint:
    def test_path_field_appears_in_hint(self):
        """Tools using 'path' (read_file, glob) get the Re-read hint."""
        handler = APIToolFormatHandler()
        parsed = ParsedMessage(
            tool_calls=[
                ParsedToolCall(
                    tool_name="read_file",
                    call_id="tc1",
                    raw_args={
                        "_truncated": True,
                        "_original_bytes": 2048,
                        "path": "/project/main.py",
                    },
                )
            ]
        )
        from drydock.core.tools.builtins.read_file import ReadFile
        tm = _make_tool_manager_with("read_file", ReadFile)
        result = handler.resolve_tool_calls(parsed, tm)
        assert len(result.failed_calls) == 1
        assert "/project/main.py" in result.failed_calls[0].error
        assert "Re-read" in result.failed_calls[0].error

    def test_file_path_field_appears_in_hint(self):
        """Tools using 'file_path' (write_file, search_replace) also get the hint."""
        handler = APIToolFormatHandler()
        parsed = ParsedMessage(
            tool_calls=[
                ParsedToolCall(
                    tool_name="write_file",
                    call_id="tc2",
                    raw_args={
                        "_truncated": True,
                        "_original_bytes": 4096,
                        "file_path": "/project/utils.py",
                    },
                )
            ]
        )
        tm = _make_tool_manager_with("write_file", _make_write_file_class())
        result = handler.resolve_tool_calls(parsed, tm)
        assert len(result.failed_calls) == 1
        assert "/project/utils.py" in result.failed_calls[0].error
        assert "Re-read" in result.failed_calls[0].error

    def test_no_path_field_gives_no_hint(self):
        """Truncated args without any path field produce no Re-read clause."""
        handler = APIToolFormatHandler()
        parsed = ParsedMessage(
            tool_calls=[
                ParsedToolCall(
                    tool_name="write_file",
                    call_id="tc3",
                    raw_args={
                        "_truncated": True,
                        "_original_bytes": 1024,
                    },
                )
            ]
        )
        tm = _make_tool_manager_with("write_file", _make_write_file_class())
        result = handler.resolve_tool_calls(parsed, tm)
        assert len(result.failed_calls) == 1
        assert "Re-read" not in result.failed_calls[0].error
        assert "_truncated" in result.failed_calls[0].error or "truncated" in result.failed_calls[0].error

    def test_existing_file_content_embedded(self, tmp_path):
        """When the target file exists, its content is embedded in the error
        so the model can rewrite without an extra read_file round-trip."""
        target = tmp_path / "utils.py"
        target.write_text("def hello():\n    return 42\n")

        handler = APIToolFormatHandler()
        parsed = ParsedMessage(
            tool_calls=[
                ParsedToolCall(
                    tool_name="write_file",
                    call_id="tc4",
                    raw_args={
                        "_truncated": True,
                        "_original_bytes": 4096,
                        "file_path": str(target),
                    },
                )
            ]
        )
        tm = _make_tool_manager_with("write_file", _make_write_file_class())
        result = handler.resolve_tool_calls(parsed, tm)
        assert len(result.failed_calls) == 1
        err = result.failed_calls[0].error
        # Content should be embedded, not just a Re-read directive
        assert "def hello():" in err
        assert "return 42" in err
        # Re-read advisory should NOT appear when content is embedded
        assert "Re-read" not in err

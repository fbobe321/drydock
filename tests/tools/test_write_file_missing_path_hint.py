"""Regression test: write_file missing path gives actionable error, not raw Pydantic noise.

Admiral logs on 2026-04-28 showed Gemma 4 calling write_file(content="...") without
a path parameter, then retrying identically 3+ times after the generic Pydantic
"Invalid arguments: 1 validation error for WriteFileArgs path Field required" message.
Fix in format.py: detect missing-path pattern and return a short, prescriptive error.
"""
from __future__ import annotations

from drydock.core.llm.format import APIToolFormatHandler, ParsedMessage, ParsedToolCall
from drydock.core.tools.builtins.write_file import WriteFile


def _make_handler() -> APIToolFormatHandler:
    from unittest.mock import MagicMock
    handler = APIToolFormatHandler()
    handler._tool_manager = MagicMock()
    handler._tool_manager.available_tools = {"write_file": WriteFile}
    return handler


def _parsed_msg(args: dict) -> ParsedMessage:
    return ParsedMessage(
        tool_calls=[
            ParsedToolCall(
                tool_name="write_file",
                call_id="call_1",
                raw_args=args,
            )
        ],
        failed_calls=[],
    )


class TestWriteFileMissingPathHint:
    def test_missing_path_returns_actionable_error(self):
        """write_file(content=...) without path yields a clear, concise error."""
        from unittest.mock import MagicMock
        tm = MagicMock()
        tm.available_tools = {"write_file": WriteFile}

        handler = APIToolFormatHandler()
        msg = _parsed_msg({"content": "print('hello')"})
        resolved = handler.resolve_tool_calls(msg, tm)

        assert len(resolved.failed_calls) == 1
        error = resolved.failed_calls[0].error
        assert "path" in error
        assert "write_file" in error
        # Must NOT be the raw Pydantic multi-line dump
        assert "Field required" not in error
        assert "input_url" not in error

    def test_missing_path_error_contains_example(self):
        """The actionable error should include an example invocation."""
        from unittest.mock import MagicMock
        tm = MagicMock()
        tm.available_tools = {"write_file": WriteFile}

        handler = APIToolFormatHandler()
        msg = _parsed_msg({"content": "x = 1"})
        resolved = handler.resolve_tool_calls(msg, tm)

        error = resolved.failed_calls[0].error
        assert "path=" in error or "`path`" in error

    def test_write_file_with_valid_args_resolves(self):
        """Sanity: write_file with both path and content resolves without error."""
        from unittest.mock import MagicMock
        tm = MagicMock()
        tm.available_tools = {"write_file": WriteFile}

        handler = APIToolFormatHandler()
        msg = _parsed_msg({"path": "hello.py", "content": "x=1"})
        resolved = handler.resolve_tool_calls(msg, tm)

        assert len(resolved.failed_calls) == 0
        assert len(resolved.tool_calls) == 1

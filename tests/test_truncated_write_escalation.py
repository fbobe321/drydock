"""Regression test: truncated write_file retry loop escalation.

When Gemma 4 calls write_file with _truncated args (copied from a pruned history
entry), drydock returns a FailedToolCall. The model often retries identically
instead of correcting the call, creating an 8-10 turn loop (observed in stress run
2026-04-29, ~8 fires of retry_after_error:write_file:truncated history in 7 min).

Fix: APIToolFormatHandler tracks per-path truncated-arg hit count.  On the 2nd+
identical failure for the same path, the error message is escalated with a project
file listing and a concrete directive to type fresh content or use search_replace.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from drydock.core.llm.format import APIToolFormatHandler, ParsedMessage, ParsedToolCall
from drydock.core.tools.builtins.write_file import WriteFile


def _make_tm():
    tm = MagicMock()
    tm.available_tools = {"write_file": WriteFile}
    return tm


def _truncated_call(path: str, call_id: str = "tc1") -> ParsedMessage:
    return ParsedMessage(
        tool_calls=[
            ParsedToolCall(
                tool_name="write_file",
                call_id=call_id,
                raw_args={
                    "_truncated": True,
                    "_original_bytes": 4096,
                    "file_path": path,
                },
            )
        ]
    )


class TestTruncatedWriteEscalation:
    def test_first_hit_no_escalation(self, tmp_path):
        """First truncated-arg failure returns normal advisory, no escalation block."""
        target = tmp_path / "cli.py"
        target.write_text("print('hello')\n")
        handler = APIToolFormatHandler()
        result = handler.resolve_tool_calls(_truncated_call(str(target)), _make_tm())
        err = result.failed_calls[0].error
        assert "REPEATED FAILURE" not in err
        assert "truncated" in err.lower()

    def test_second_hit_escalates(self, tmp_path):
        """Second truncated-arg failure for the same path triggers escalation block."""
        target = tmp_path / "cli.py"
        target.write_text("print('hello')\n")
        handler = APIToolFormatHandler()
        # First call
        handler.resolve_tool_calls(_truncated_call(str(target), "tc1"), _make_tm())
        # Second call — should escalate
        result = handler.resolve_tool_calls(_truncated_call(str(target), "tc2"), _make_tm())
        err = result.failed_calls[0].error
        assert "REPEATED FAILURE #2" in err
        assert "stale truncated template" in err
        assert "search_replace" in err

    def test_third_hit_increments_count(self, tmp_path):
        """Third hit shows #3 in the escalation message."""
        target = tmp_path / "utils.py"
        target.write_text("x = 1\n")
        handler = APIToolFormatHandler()
        for i in range(3):
            result = handler.resolve_tool_calls(
                _truncated_call(str(target), f"tc{i}"), _make_tm()
            )
        err = result.failed_calls[0].error
        assert "REPEATED FAILURE #3" in err

    def test_different_paths_tracked_separately(self, tmp_path):
        """Two different paths each have their own hit counter."""
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_text("pass\n")
        b.write_text("pass\n")
        handler = APIToolFormatHandler()
        # Hit path a twice → escalate
        handler.resolve_tool_calls(_truncated_call(str(a), "tc1"), _make_tm())
        result_a = handler.resolve_tool_calls(_truncated_call(str(a), "tc2"), _make_tm())
        assert "REPEATED FAILURE #2" in result_a.failed_calls[0].error
        # Hit path b once → no escalation
        result_b = handler.resolve_tool_calls(_truncated_call(str(b), "tc3"), _make_tm())
        assert "REPEATED FAILURE" not in result_b.failed_calls[0].error

    def test_fresh_handler_resets_count(self, tmp_path):
        """A new APIToolFormatHandler instance starts with a clean counter."""
        target = tmp_path / "main.py"
        target.write_text("pass\n")
        handler1 = APIToolFormatHandler()
        handler1.resolve_tool_calls(_truncated_call(str(target), "tc1"), _make_tm())
        handler1.resolve_tool_calls(_truncated_call(str(target), "tc2"), _make_tm())
        # Fresh handler for a new session — no accumulated state
        handler2 = APIToolFormatHandler()
        result = handler2.resolve_tool_calls(_truncated_call(str(target), "tc3"), _make_tm())
        assert "REPEATED FAILURE" not in result.failed_calls[0].error

"""Regression test: truncated search_replace escalation uses correct tool name.

When Gemma 4 calls search_replace with _truncated args, the escalation message
must say "search_replace call" and give SEARCH/REPLACE recovery guidance —
not "write_file call" and "call write_file with typed content".

The wrong tool name in the escalation confused the model into switching to
write_file (full overwrite) instead of reading the file and forming fresh
SEARCH/REPLACE blocks. Observed via admiral retry_after_error:search_replace
loops in stress run 2026-04-29.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from drydock.core.llm.format import APIToolFormatHandler, ParsedMessage, ParsedToolCall
from drydock.core.tools.builtins.search_replace import SearchReplace


def _make_tm():
    tm = MagicMock()
    tm.available_tools = {"search_replace": SearchReplace}
    return tm


def _truncated_sr_call(path: str, call_id: str = "tc1") -> ParsedMessage:
    return ParsedMessage(
        tool_calls=[
            ParsedToolCall(
                tool_name="search_replace",
                call_id=call_id,
                raw_args={
                    "_truncated": True,
                    "_original_bytes": 1230,
                    "file_path": path,
                },
            )
        ]
    )


class TestTruncatedSearchReplaceEscalation:
    def test_first_hit_mentions_search_replace(self, tmp_path):
        """First truncated-arg failure for search_replace mentions the right tool."""
        target = tmp_path / "cli.py"
        target.write_text("def run(): pass\n")
        handler = APIToolFormatHandler()
        result = handler.resolve_tool_calls(_truncated_sr_call(str(target)), _make_tm())
        err = result.failed_calls[0].error
        assert "truncated" in err.lower()
        assert "REPEATED FAILURE" not in err

    def test_second_hit_escalates_with_correct_tool_name(self, tmp_path):
        """Escalation message says 'search_replace call', not 'write_file call'."""
        target = tmp_path / "utils.py"
        target.write_text("x = 1\n")
        handler = APIToolFormatHandler()
        handler.resolve_tool_calls(_truncated_sr_call(str(target), "tc1"), _make_tm())
        result = handler.resolve_tool_calls(_truncated_sr_call(str(target), "tc2"), _make_tm())
        err = result.failed_calls[0].error
        assert "REPEATED FAILURE #2" in err
        assert "search_replace call" in err
        assert "write_file call" not in err

    def test_second_hit_escalation_gives_search_replace_recovery(self, tmp_path):
        """Escalation for search_replace suggests SEARCH/REPLACE markers, not write_file."""
        target = tmp_path / "agent.py"
        target.write_text("def run(): pass\n")
        handler = APIToolFormatHandler()
        handler.resolve_tool_calls(_truncated_sr_call(str(target), "tc1"), _make_tm())
        result = handler.resolve_tool_calls(_truncated_sr_call(str(target), "tc2"), _make_tm())
        err = result.failed_calls[0].error
        assert "SEARCH" in err
        assert "REPLACE" in err
        # Must NOT redirect to write_file as the primary recovery path
        assert "then call write_file" not in err

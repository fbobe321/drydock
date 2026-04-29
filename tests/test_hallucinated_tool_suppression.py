"""Regression test: hallucinated tool calls (e.g. ralph_repo_index) must
produce a tool result message in the history so the conversation stays
well-formed. Before the fix, _IGNORE_TOOLS entries were silently dropped via
`continue`, leaving the assistant tool_call with no paired tool result.
This caused empty_after_tool loops because the model kept waiting for a
result that never arrived."""
import pytest
from unittest.mock import MagicMock

from drydock.core.llm.format import (
    APIToolFormatHandler,
    FailedToolCall,
    ParsedMessage,
    ParsedToolCall,
    ResolvedMessage,
)


def _make_handler():
    return APIToolFormatHandler()


def _make_tool_manager(available: list[str]):
    tm = MagicMock()
    tm.available_tools = {name: MagicMock() for name in available}
    return tm


def _parsed(tool_name: str, call_id: str = "c1") -> ParsedMessage:
    return ParsedMessage(
        tool_calls=[ParsedToolCall(tool_name=tool_name, call_id=call_id, raw_args={})]
    )


HALLUCINATED_TOOLS = [
    "ralph_repo_index",
    "repo_index",
    "index_repo",
    "exit_plan_mode",
    "enter_plan_mode",
    "plan_mode",
    "list_mcp_resources",
    "list_resources",
    "search_resources",
]


@pytest.mark.parametrize("tool_name", HALLUCINATED_TOOLS)
def test_hallucinated_tool_goes_to_suppressed_failures(tool_name):
    handler = _make_handler()
    tm = _make_tool_manager(["read_file", "write_file"])
    parsed = _parsed(tool_name)
    resolved = handler.resolve_tool_calls(parsed, tm)

    # Must NOT appear as a visible failed_call (no TUI error shown)
    assert all(fc.tool_name != tool_name for fc in resolved.failed_calls), (
        f"{tool_name} should not appear in failed_calls (would show TUI error)"
    )
    # MUST appear in suppressed_failures (keeps message history well-formed)
    assert any(sf.tool_name == tool_name for sf in resolved.suppressed_failures), (
        f"{tool_name} must be in suppressed_failures so a tool result is added to history"
    )
    # The suppressed entry must have a non-empty error message
    sf = next(sf for sf in resolved.suppressed_failures if sf.tool_name == tool_name)
    assert sf.error, "suppressed failure must have an error message telling model what to use"
    # Must not appear in resolved tool_calls
    assert all(tc.tool_name != tool_name for tc in resolved.tool_calls)


def test_suppressed_failures_included_in_resolved_message():
    handler = _make_handler()
    tm = _make_tool_manager(["read_file"])
    parsed = _parsed("ralph_repo_index")
    resolved = handler.resolve_tool_calls(parsed, tm)
    assert isinstance(resolved, ResolvedMessage)
    assert len(resolved.suppressed_failures) == 1
    assert resolved.suppressed_failures[0].call_id == "c1"


def test_unknown_non_hallucinated_tool_goes_to_failed_calls():
    """A completely unknown tool name should be a visible FailedToolCall."""
    handler = _make_handler()
    tm = _make_tool_manager(["read_file"])
    parsed = _parsed("nonexistent_random_tool")
    resolved = handler.resolve_tool_calls(parsed, tm)
    assert any(fc.tool_name == "nonexistent_random_tool" for fc in resolved.failed_calls)
    assert not resolved.suppressed_failures

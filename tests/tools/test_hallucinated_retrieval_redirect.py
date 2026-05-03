"""Regression test: hallucinated retrieval tool redirects to `retrieve`.

Dispatch queue 2026-05-03: `harness:tool:hallucinated_name` fires repeatedly
for `ralph_repo_index`. The old canned response said "use glob/grep/read_file"
but the model wanted RETRIEVAL — it kept calling ralph_repo_index anyway,
causing empty_after_tool loops that blocked TUI input for 4+ minutes per
session (112 SKIPs out of 730 prompts, write rate 38% vs 74% baseline).

Fix: when a retrieval-flavored hallucinated tool is called and `retrieve` IS
registered, the canned error must redirect to `retrieve(query=...)` instead
of glob/grep. This satisfies the model's retrieval intent and breaks the loop.
"""
from __future__ import annotations

from drydock.core.llm.format import APIToolFormatHandler, ParsedMessage, ParsedToolCall


class _FakeToolClass:
    """Minimal stand-in for a tool class (only the name lookup matters)."""


class _FakeToolManager:
    """Duck-typed ToolManager mock; only `available_tools` is used."""

    def __init__(self, tool_names: list[str]) -> None:
        self.available_tools: dict[str, type] = {
            name: _FakeToolClass for name in tool_names  # type: ignore[misc]
        }


def _make_parsed(tool_name: str) -> ParsedMessage:
    return ParsedMessage(
        text="",
        tool_calls=[
            ParsedToolCall(tool_name=tool_name, call_id="c1", raw_args={})
        ],
    )


def _resolve(tool_name: str, registered_tools: list[str]):
    handler = APIToolFormatHandler()
    mgr = _FakeToolManager(registered_tools)
    parsed = _make_parsed(tool_name)
    return handler.resolve_tool_calls(parsed, mgr)  # type: ignore[arg-type]


class TestRetrievalHallucinationRedirect:
    def test_ralph_repo_index_redirects_to_retrieve_when_available(self):
        tools = ["glob", "grep", "read_file", "retrieve", "bash"]
        resolved = _resolve("ralph_repo_index", tools)
        assert not resolved.tool_calls
        assert not resolved.failed_calls
        assert len(resolved.suppressed_failures) == 1
        error = resolved.suppressed_failures[0].error
        assert "retrieve" in error
        assert "query" in error
        # Must NOT direct model back to glob/read_file for a retrieval intent
        assert "glob" not in error

    def test_ralph_repo_index_falls_back_when_retrieve_not_available(self):
        tools = ["glob", "grep", "read_file", "bash"]
        resolved = _resolve("ralph_repo_index", tools)
        assert len(resolved.suppressed_failures) == 1
        error = resolved.suppressed_failures[0].error
        # Falls back to the generic message
        assert "glob" in error or "read_file" in error
        assert "retrieve" not in error

    def test_exit_plan_mode_not_affected(self):
        """exit_plan_mode is not a retrieval tool — should use generic message."""
        tools = ["glob", "grep", "read_file", "retrieve", "bash"]
        resolved = _resolve("exit_plan_mode", tools)
        assert len(resolved.suppressed_failures) == 1
        error = resolved.suppressed_failures[0].error
        # Generic message, not the retrieve redirect
        assert "does not exist" in error

    def test_repo_index_also_redirects(self):
        tools = ["glob", "grep", "retrieve"]
        resolved = _resolve("repo_index", tools)
        assert len(resolved.suppressed_failures) == 1
        assert "retrieve" in resolved.suppressed_failures[0].error

    def test_ralph_file_summary_redirects(self):
        tools = ["glob", "grep", "retrieve"]
        resolved = _resolve("ralph_file_summary", tools)
        assert len(resolved.suppressed_failures) == 1
        assert "retrieve" in resolved.suppressed_failures[0].error

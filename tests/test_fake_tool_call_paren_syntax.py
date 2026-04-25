"""Regression tests for issue #11: Gemma 4 emits Python-call tool syntax as text.

The model legitimately had access to the `task` tool but produced
``task(task="Explore the project structure", agent="explore")`` as plain
content with no real ``tool_calls`` field, so nothing ran and the TUI
looked dead. format.APIToolFormatHandler.process_api_response_message
already nukes the ``call:name{...}`` shape; this adds the
``name(arg=...)`` shape Gemma 4 emits when it regresses to Python syntax.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from drydock.core.llm.format import APIToolFormatHandler


def _msg(content: str, *, tool_calls=None, role: str = "assistant") -> MagicMock:
    m = MagicMock(spec=["role", "content", "tool_calls", "reasoning_content", "reasoning_signature"])
    m.role = role
    m.content = content
    m.tool_calls = tool_calls
    m.reasoning_content = None
    m.reasoning_signature = None
    return m


@pytest.fixture
def handler():
    return APIToolFormatHandler()


class TestParenSyntaxNuker:
    def test_task_call_python_syntax_is_nuked(self, handler):
        # Exact text from issue #11 screenshot.
        text = 'task(task="Explore the project structure and summarize the purpose of each module", agent="explore")'
        out = handler.process_api_response_message(_msg(text))
        assert out.content is None

    def test_multiline_args_are_nuked(self, handler):
        text = 'task(task="Explore\nmulti-line\ndescription", agent="explore")'
        out = handler.process_api_response_message(_msg(text))
        assert out.content is None

    def test_leading_thought_prefix_then_call_nuked(self, handler):
        text = 'thought\n\ntask(task="hi", agent="explore")'
        out = handler.process_api_response_message(_msg(text))
        assert out.content is None

    def test_real_tool_calls_present_does_not_nuke_content(self, handler):
        # When a real tool_call IS present, leave content alone — the
        # model can legitimately emit text alongside a real call.
        fake_tc = MagicMock()
        fake_tc.id = "tc1"
        fake_tc.index = 0
        fake_tc.function.name = "task"
        fake_tc.function.arguments = '{"task": "x", "agent": "explore"}'
        text = 'task(task="hi", agent="explore")'
        out = handler.process_api_response_message(_msg(text, tool_calls=[fake_tc]))
        # Content is preserved (still useful for stripping/etc), but the
        # real tool_calls field carries through.
        assert out.tool_calls is not None
        assert len(out.tool_calls) == 1

    def test_prose_with_embedded_function_call_not_nuked(self, handler):
        # Non-bug case: user prose that contains "func(x=1)" should NOT
        # be nuked because the WHOLE content is not the call shape.
        text = "I will use the task() function with task='foo' to explore."
        out = handler.process_api_response_message(_msg(text))
        assert out.content == text

    def test_normal_text_response_not_nuked(self, handler):
        text = "Hello! I'll help you with that."
        out = handler.process_api_response_message(_msg(text))
        assert out.content == text

    def test_code_block_with_function_call_not_nuked(self, handler):
        # If the call shape is preceded by prose, the regex's start-anchor
        # won't match the whole content, so it stays. Only entire-content
        # function calls trigger.
        text = "Sure, here's an example:\n\ntask(task='x', agent='explore')"
        out = handler.process_api_response_message(_msg(text))
        assert out.content == text

    def test_existing_call_brace_syntax_still_nuked(self, handler):
        # Don't regress the v2.6.91 nuker.
        text = "call:write_file{content: import os ..."
        out = handler.process_api_response_message(_msg(text))
        assert out.content is None

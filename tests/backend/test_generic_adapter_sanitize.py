"""Regression tests for OpenAIAdapter control-char sanitization (issue #13).

Repro: a bash tool result containing a NUL or ESC byte rides through the
conversation history. On the next /v1/chat/completions request vLLM parses
the outer JSON fine, but its tool-call parser re-parses
``tool_calls.function.arguments`` as JSON and the literal control char
makes that second-level parse 400 with "Invalid control character at line 1
col N". /compact does not remove the offending message; only /clear does.
"""
from __future__ import annotations

import json

import pytest

from drydock.core.config import ProviderConfig
from drydock.core.llm.backend.generic import OpenAIAdapter
from drydock.core.types import (
    FunctionCall,
    LLMMessage,
    Role,
    ToolCall,
)

NUL = chr(0)
ESC = chr(27)
BEL = chr(7)
BS = chr(8)


@pytest.fixture
def adapter():
    return OpenAIAdapter()


@pytest.fixture
def provider():
    return ProviderConfig(
        name="vllm-local",
        api_base="http://localhost:8000/v1",
        api_key_env_var="LOCAL_API_KEY",
        api_style="generic",
    )


def _prepare(adapter, provider, messages):
    req = adapter.prepare_request(
        model_name="gemma4",
        messages=messages,
        temperature=0,
        tools=None,
        max_tokens=None,
        tool_choice=None,
        enable_streaming=False,
        provider=provider,
    )
    return req.body


class TestStripControlChars:
    def test_strips_nul_and_esc_from_string(self):
        s = f"hello{NUL}world{ESC}bad{BEL}{BS}keep\ttab\nnewline"
        out = OpenAIAdapter._strip_control_chars(s)
        assert NUL not in out
        assert ESC not in out
        assert BEL not in out
        assert BS not in out
        assert "\t" in out
        assert "\n" in out
        assert out == "helloworldbadkeep\ttab\nnewline"

    def test_recurses_into_dict(self):
        d = {"a": f"x{NUL}y", "b": {"c": f"z{ESC}w"}}
        assert OpenAIAdapter._strip_control_chars(d) == {"a": "xy", "b": {"c": "zw"}}

    def test_recurses_into_list(self):
        d = [f"a{NUL}b", {"k": f"c{ESC}d"}, 42, None]
        assert OpenAIAdapter._strip_control_chars(d) == ["ab", {"k": "cd"}, 42, None]

    def test_passthrough_non_string(self):
        for v in (None, 1, 1.5, True, False):
            assert OpenAIAdapter._strip_control_chars(v) is v


class TestPrepareRequestSanitizes:
    def test_user_message_content_stripped(self, adapter, provider):
        msgs = [LLMMessage(role=Role.user, content=f"hi{NUL}there{ESC}")]
        body_bytes = _prepare(adapter, provider, msgs)
        body = json.loads(body_bytes)
        assert body["messages"][0]["content"] == "hithere"

    def test_tool_result_content_stripped(self, adapter, provider):
        msgs = [
            LLMMessage(role=Role.user, content="run it"),
            LLMMessage(
                role=Role.assistant,
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        function=FunctionCall(name="bash", arguments='{"cmd": "cat foo"}'),
                    )
                ],
            ),
            LLMMessage(
                role=Role.tool,
                tool_call_id="call_1",
                name="bash",
                content=f"binary{NUL}garbage{ESC}here",
            ),
        ]
        body_bytes = _prepare(adapter, provider, msgs)
        body = json.loads(body_bytes)
        tool_msg = body["messages"][-1]
        assert tool_msg["role"] == "tool"
        assert NUL not in tool_msg["content"]
        assert ESC not in tool_msg["content"]
        assert tool_msg["content"] == "binarygarbagehere"

    def test_tool_call_arguments_stripped(self, adapter, provider):
        # The actual cause of #13: arguments string with embedded control
        # chars. vLLM re-parses arguments as JSON; raw NUL inside the
        # parsed string blows up the second-level json.loads.
        bad_args = '{"cmd": "echo bad"}' + NUL + ESC
        msgs = [
            LLMMessage(role=Role.user, content="ok"),
            LLMMessage(
                role=Role.assistant,
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_2",
                        function=FunctionCall(name="bash", arguments=bad_args),
                    )
                ],
            ),
        ]
        body_bytes = _prepare(adapter, provider, msgs)
        body = json.loads(body_bytes)
        args_str = body["messages"][-1]["tool_calls"][0]["function"]["arguments"]
        assert NUL not in args_str
        assert ESC not in args_str

    def test_no_control_bytes_in_serialized_body(self, adapter, provider):
        msgs = [
            LLMMessage(role=Role.system, content="be helpful"),
            LLMMessage(role=Role.user, content=f"dirty{NUL}input"),
            LLMMessage(
                role=Role.tool,
                tool_call_id="x",
                name="bash",
                content=f"{ESC}[31merror{ESC}[0m{NUL}",
            ),
        ]
        body_bytes = _prepare(adapter, provider, msgs)
        # Outer body must be ASCII and have zero raw control bytes other
        # than JSON whitespace.
        for b in body_bytes:
            assert b >= 0x20 or b in (0x09, 0x0A, 0x0D), f"raw control byte 0x{b:02x} in body"

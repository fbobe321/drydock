"""Regression test for the stress-run 400 spiral on 2026-04-25.

Repro: `_truncate_old_tool_results` truncates assistant tool_call
arguments by appending `"\\n[…truncated N bytes…]"` directly into the
JSON STRING that vLLM later re-parses as JSON for the tool-call
arguments. A raw `\\n` byte inside a JSON string is invalid JSON; vLLM's
parser 400s with "Invalid control character at line 1 col 401". The
old run could go ~30 prompts before truncation kicked in, then 100+
400s before recovery.

Fix: build a small VALID JSON stub from the original (parsed) args
keeping just one identifying field. The stub must round-trip through
json.loads cleanly, including via vLLM's strict parser.
"""
from __future__ import annotations

import json

import pytest

from drydock.core.types import (
    FunctionCall,
    LLMMessage,
    Role,
    ToolCall,
)
from tests.conftest import build_test_agent_loop


def _make_tc(name: str, arguments: str, idx: int = 0) -> ToolCall:
    return ToolCall(
        id=f"tc_{idx}",
        index=idx,
        function=FunctionCall(name=name, arguments=arguments),
    )


def _build_history(num_old: int) -> list[LLMMessage]:
    """Build a conversation with `num_old` old assistant-with-tools
    messages plus 8 newer messages (so KEEP_RECENT=6 plus margin)."""
    msgs: list[LLMMessage] = [
        LLMMessage(role=Role.system, content="be helpful"),
        LLMMessage(role=Role.user, content="please help"),
    ]
    # Old assistant-with-tools that should be truncated.
    big_content = "x" * 5000
    big_args = json.dumps({
        "file_path": "/data3/test/foo.py",
        "content": big_content,
    })
    for i in range(num_old):
        msgs.append(
            LLMMessage(
                role=Role.assistant,
                content="",
                tool_calls=[_make_tc("write_file", big_args, i)],
            )
        )
        msgs.append(
            LLMMessage(
                role=Role.tool,
                tool_call_id=f"tc_{i}",
                name="write_file",
                content=f"Wrote {i}",
            )
        )
    # 8 newer messages — keep these full.
    for i in range(8):
        msgs.append(
            LLMMessage(
                role=Role.assistant,
                content="",
                tool_calls=[_make_tc("read_file", '{"path":"/x"}', 100 + i)],
            )
        )
        msgs.append(
            LLMMessage(
                role=Role.tool,
                tool_call_id=f"tc_{100+i}",
                name="read_file",
                content="ok",
            )
        )
    return msgs


class TestTruncateArgsValidJson:
    def test_truncated_args_remain_valid_json(self):
        loop = build_test_agent_loop()
        loop.messages.reset(_build_history(num_old=10))

        loop._truncate_old_tool_results()

        # All assistant tool_calls.arguments must parse as valid JSON.
        for m in loop.messages:
            if m.role != Role.assistant or not m.tool_calls:
                continue
            for tc in m.tool_calls:
                args = tc.function.arguments or ""
                if not args:
                    continue
                # Must round-trip cleanly via the strict json parser.
                parsed = json.loads(args)  # raises on invalid

    def test_truncated_args_have_no_raw_control_chars(self):
        loop = build_test_agent_loop()
        loop.messages.reset(_build_history(num_old=10))

        loop._truncate_old_tool_results()

        for m in loop.messages:
            if m.role != Role.assistant or not m.tool_calls:
                continue
            for tc in m.tool_calls:
                args = (tc.function.arguments or "").encode("utf-8")
                bad = [b for b in args if b < 0x20 and b not in (9, 10, 13)]
                assert not bad, (
                    f"arguments still has raw control bytes: "
                    f"{[hex(b) for b in bad[:5]]} in {tc.function.arguments!r}"
                )

    def test_truncated_args_keep_identifying_field(self):
        loop = build_test_agent_loop()
        loop.messages.reset(_build_history(num_old=10))

        loop._truncate_old_tool_results()

        # The OLDEST assistant-with-tools message should be truncated;
        # its stub should retain file_path so the model can still
        # remember "I wrote /data3/test/foo.py earlier".
        old_assistants = [
            m for m in loop.messages
            if m.role == Role.assistant and m.tool_calls
        ]
        # First few are the OLD ones (truncated).
        oldest = old_assistants[0]
        args = json.loads(oldest.tool_calls[0].function.arguments)
        assert args.get("_truncated") is True
        assert args.get("file_path") == "/data3/test/foo.py"
        assert "_original_bytes" in args

    def test_recent_args_not_touched(self):
        loop = build_test_agent_loop()
        loop.messages.reset(_build_history(num_old=10))

        loop._truncate_old_tool_results()

        # The LATEST 6 assistant-with-tools messages should keep their
        # full args. Find them by argument size — newer ones use a small
        # `'{"path":"/x"}'`, which stays untouched.
        old_assistants = [
            m for m in loop.messages
            if m.role == Role.assistant and m.tool_calls
        ]
        recent = old_assistants[-6:]
        for m in recent:
            args = m.tool_calls[0].function.arguments
            # Should NOT have the truncation marker.
            assert '"_truncated"' not in args, (
                f"recent args were truncated unexpectedly: {args[:120]}"
            )

    def test_idempotent_on_already_truncated(self):
        loop = build_test_agent_loop()
        loop.messages.reset(_build_history(num_old=10))

        loop._truncate_old_tool_results()
        # Snapshot truncated args.
        first_pass = [
            tc.function.arguments
            for m in loop.messages if m.role == Role.assistant and m.tool_calls
            for tc in m.tool_calls
        ]
        # Second pass — should be a no-op for already-truncated entries.
        loop._truncate_old_tool_results()
        second_pass = [
            tc.function.arguments
            for m in loop.messages if m.role == Role.assistant and m.tool_calls
            for tc in m.tool_calls
        ]
        assert first_pass == second_pass

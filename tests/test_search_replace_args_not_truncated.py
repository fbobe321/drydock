"""Regression test: search_replace call args must NOT be truncated.

Repro: _truncate_old_tool_results truncated ALL large tool-call arguments,
including search_replace SEARCH/REPLACE blocks (~400-800 bytes). The model
then saw {_truncated: true, file_path: "..."} in its own history and copied
those truncated args as new call arguments. format.py detected this and
returned a FailedToolCall, but the model retried with the same truncated
args again and again, creating a retry_after_error:search_replace loop.

Fix (2026-04-29): skip the arg-truncation loop for search_replace tool
calls. Only write_file carries full file content worth truncating.
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


def _build_history_with_search_replace(num_old: int) -> list[LLMMessage]:
    """Build a conversation with `num_old` old search_replace messages
    plus enough newer messages to exceed KEEP_RECENT."""
    msgs: list[LLMMessage] = [
        LLMMessage(role=Role.system, content="be helpful"),
        LLMMessage(role=Role.user, content="please help"),
    ]
    # search_replace calls with realistic 600+ byte args (exceeds SOFT_CAP=500)
    search_block = "    def run_tool(self, tool_name: str, args: dict) -> str:\n"
    search_block += "        # This is the old implementation that needs to be replaced\n"
    search_block += "        return ''\n"
    replace_block = "    def run_tool(self, tool_name: str, args: dict) -> str:\n"
    replace_block += "        plugin = self._load_plugin(tool_name)\n"
    replace_block += "        if plugin is None:\n"
    replace_block += "            raise ValueError(f'Unknown tool: {tool_name}')\n"
    replace_block += "        return plugin.execute(args)\n"
    sr_args = json.dumps({
        "file_path": "/data3/drydock_test_projects/403_tool_agent/tool_agent/cli.py",
        "content": (
            f"<<<<<<< SEARCH\n{search_block}=======\n{replace_block}>>>>>>> REPLACE"
        ),
    })
    assert len(sr_args) > 500, f"Test args must exceed SOFT_CAP_BYTES=500 to be meaningful, got {len(sr_args)}"

    for i in range(num_old):
        msgs.append(
            LLMMessage(
                role=Role.assistant,
                content="",
                tool_calls=[_make_tc("search_replace", sr_args, i)],
            )
        )
        msgs.append(
            LLMMessage(
                role=Role.tool,
                tool_call_id=f"tc_{i}",
                name="search_replace",
                content="Applied edit.",
            )
        )

    # Add newer messages to exceed KEEP_RECENT (4) so truncation would trigger
    # for write_file but should be skipped for search_replace.
    for i in range(6):
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


class TestSearchReplaceArgsNotTruncated:
    def test_search_replace_args_preserved_after_truncation(self):
        """search_replace call args must not be replaced with {_truncated: true} stub."""
        loop = build_test_agent_loop()
        loop.messages.reset(_build_history_with_search_replace(num_old=10))

        loop._truncate_old_tool_results()

        for m in loop.messages:
            if m.role != Role.assistant or not m.tool_calls:
                continue
            for tc in m.tool_calls:
                if tc.function.name != "search_replace":
                    continue
                args = tc.function.arguments or ""
                parsed = json.loads(args)
                assert not parsed.get("_truncated"), (
                    f"search_replace args were truncated — model will copy the stub "
                    f"and loop: {args[:120]}"
                )
                assert "content" in parsed, (
                    "search_replace args lost the 'content' field after truncation"
                )

    def test_write_file_still_truncated(self):
        """write_file args should still be truncated (they carry full file content)."""
        msgs: list[LLMMessage] = [
            LLMMessage(role=Role.system, content="be helpful"),
            LLMMessage(role=Role.user, content="please help"),
        ]
        big_args = json.dumps({
            "file_path": "/data3/test/foo.py",
            "content": "x" * 5000,
        })
        for i in range(10):
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
        for i in range(6):
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

        loop = build_test_agent_loop()
        loop.messages.reset(msgs)
        loop._truncate_old_tool_results()

        truncated_count = 0
        for m in loop.messages:
            if m.role != Role.assistant or not m.tool_calls:
                continue
            for tc in m.tool_calls:
                if tc.function.name != "write_file":
                    continue
                args = tc.function.arguments or ""
                parsed = json.loads(args)
                if parsed.get("_truncated"):
                    truncated_count += 1

        assert truncated_count > 0, "write_file args should still be truncated"

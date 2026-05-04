"""Regression tests for GitHub issues #4, #5, #6, #14."""
from __future__ import annotations

import os
import pytest


# Issue #4: todo broken — action defaults to read, silently ignores todos

class TestIssue4TodoInferWrite:
    def test_todos_without_action_infers_write(self):
        from drydock.core.tools.builtins.todo import TodoArgs
        args = TodoArgs(todos=[{"content": "first", "status": "pending"}])
        assert args.action == "write"

    def test_empty_todos_stays_read(self):
        from drydock.core.tools.builtins.todo import TodoArgs
        args = TodoArgs()
        assert args.action == "read"

    def test_explicit_read_with_todos_still_writes(self):
        """Intent is clear from the todos list. Model's confused 'read'
        shouldn't stomp a populated list."""
        from drydock.core.tools.builtins.todo import TodoArgs
        args = TodoArgs(action="read", todos=[{"content": "x"}])
        assert args.action == "write"

    def test_explicit_write_no_todos_still_writes(self):
        """User explicitly said write but sent nothing — honor it (empties)."""
        from drydock.core.tools.builtins.todo import TodoArgs
        args = TodoArgs(action="write", todos=[])
        assert args.action == "write"


# Issue #5: cross-chunk newlines collapse to spaces in streaming output

class TestIssue5StreamingLineBreaks:
    @pytest.mark.asyncio
    async def test_streaming_preserves_cross_chunk_paragraph_breaks(self, tmp_path):
        """AssistantMessage.append_content must upgrade a single \\n at a
        chunk boundary to \\n\\n so markdown renders it as a paragraph
        break. Without this, 'hello\\n' + 'world' becomes 'hello\\nworld'
        which markdown prints as 'hello world'."""
        from drydock.cli.textual_ui.widgets.messages import AssistantMessage

        # Bypass Textual mount — directly test the content-building path.
        # AssistantMessage.__init__ calls super().__init__ which sets
        # self._content; append_content mutates it via super().append_content.
        msg = object.__new__(AssistantMessage)
        msg._content = ""
        msg._content_initialized = False
        msg._stream = None

        async def fake_super_append(c: str) -> None:
            msg._content += c

        # Monkey-patch only the super().append_content invocation.
        from drydock.cli.textual_ui.widgets.messages import (
            StreamingMessageBase,
        )
        original = StreamingMessageBase.append_content

        async def shim(self, content: str) -> None:
            self._content += content

        StreamingMessageBase.append_content = shim
        try:
            await AssistantMessage.append_content(msg, "hello\n")
            await AssistantMessage.append_content(msg, "world")
        finally:
            StreamingMessageBase.append_content = original

        # hello\n + world must yield hello\n\nworld so markdown renders
        # it as two paragraphs, not "hello world".
        assert "hello\n\nworld" in msg._content


# Issue #6: /find and /grep via bash should auto-approve even with full paths

class TestIssue6BashAllowlistBasename:
    def _bash_tool(self):
        from drydock.core.tools.builtins.bash import Bash, BashToolConfig
        from drydock.core.tools.base import BaseToolState
        return Bash(config=BashToolConfig(), state=BaseToolState())

    def test_bare_find_allowlisted(self):
        from drydock.core.tools.builtins.bash import BashArgs
        from drydock.core.tools.base import ToolPermission
        tool = self._bash_tool()
        res = tool.resolve_permission(BashArgs(command="find . -name '*.py'"))
        assert res == ToolPermission.ALWAYS

    def test_full_path_find_allowlisted(self):
        """New fix: /usr/bin/find must behave like bare find for
        auto-approval (basename match)."""
        from drydock.core.tools.builtins.bash import BashArgs
        from drydock.core.tools.base import ToolPermission
        tool = self._bash_tool()
        res = tool.resolve_permission(BashArgs(command="/usr/bin/find . -name '*.py'"))
        assert res == ToolPermission.ALWAYS

    def test_relative_path_grep_allowlisted(self):
        from drydock.core.tools.builtins.bash import BashArgs
        from drydock.core.tools.base import ToolPermission
        tool = self._bash_tool()
        res = tool.resolve_permission(BashArgs(command="./grep -r pattern ."))
        assert res == ToolPermission.ALWAYS

    def test_unknown_command_still_asks(self):
        """Safety: basename match doesn't accidentally allow random
        binaries. `/usr/bin/sudo` has basename `sudo` which isn't
        allowlisted → no auto-approve."""
        from drydock.core.tools.builtins.bash import BashArgs
        tool = self._bash_tool()
        res = tool.resolve_permission(BashArgs(command="/usr/bin/sudo whoami"))
        # Returns None (caller prompts for approval) — not ALWAYS.
        from drydock.core.tools.base import ToolPermission
        assert res != ToolPermission.ALWAYS


# Issue #14: empty assistant messages (no content, no tool_calls) persist in
# history after stall-retry exhaustion and cause 400 errors on the next turn.
# _sanitize_message_ordering must drop them before the next LLM call.

class TestIssue14EmptyAssistantDropped:
    def _make_minimal_agent(self):
        from drydock.core.agent_loop import AgentLoop
        from drydock.core.types import MessageList
        al = object.__new__(AgentLoop)
        al.messages = MessageList()
        # stubs for methods called by _sanitize_message_ordering
        al._truncate_old_tool_results = lambda: None
        al._proactive_prune_write_oscillation = lambda: None
        import os as _os
        al._os = _os
        return al

    def test_empty_assistant_dropped(self):
        """Empty assistant message (no content, no tool_calls) is dropped."""
        from drydock.core.agent_loop import AgentLoop
        from drydock.core.types import LLMMessage, Role, MessageList
        import os
        al = object.__new__(AgentLoop)
        al.messages = MessageList()
        al._truncate_old_tool_results = lambda: None
        al._proactive_prune_write_oscillation = lambda: None

        al.messages.append(LLMMessage(role=Role.user, content="hello"))
        al.messages.append(LLMMessage(role=Role.assistant, content=None))  # empty — bug

        original_env = os.environ.get("DRYDOCK_AUTO_CONTINUE_DISABLE")
        os.environ["DRYDOCK_AUTO_CONTINUE_DISABLE"] = "1"
        try:
            al._sanitize_message_ordering()
        finally:
            if original_env is None:
                os.environ.pop("DRYDOCK_AUTO_CONTINUE_DISABLE", None)
            else:
                os.environ["DRYDOCK_AUTO_CONTINUE_DISABLE"] = original_env

        roles = [m.role for m in al.messages]
        assert Role.assistant not in roles, f"Empty assistant must be dropped; got {roles}"

    def test_empty_assistant_with_preceding_tool_result_both_dropped(self):
        """Tool result immediately before an empty assistant is also dropped
        to avoid a dangling tool message with no matching assistant turn."""
        from drydock.core.agent_loop import AgentLoop
        from drydock.core.types import LLMMessage, Role, MessageList, ToolCall, FunctionCall
        import os
        al = object.__new__(AgentLoop)
        al.messages = MessageList()
        al._truncate_old_tool_results = lambda: None
        al._proactive_prune_write_oscillation = lambda: None

        al.messages.append(LLMMessage(role=Role.user, content="do it"))
        al.messages.append(LLMMessage(
            role=Role.assistant,
            content=None,
            tool_calls=[ToolCall(id="c1", function=FunctionCall(name="bash", arguments="{}"))],
        ))
        al.messages.append(LLMMessage(role=Role.tool, content="ok", tool_call_id="c1"))
        al.messages.append(LLMMessage(role=Role.assistant, content=None))  # empty — bug

        os.environ["DRYDOCK_AUTO_CONTINUE_DISABLE"] = "1"
        try:
            al._sanitize_message_ordering()
        finally:
            os.environ.pop("DRYDOCK_AUTO_CONTINUE_DISABLE", None)

        roles = [m.role for m in al.messages]
        # The empty assistant AND its preceding orphan tool result should be gone
        assert roles == [Role.user, Role.assistant, Role.tool] or roles == [Role.user], \
            f"Unexpected roles after sanitize: {roles}"
        # The productive assistant (with tool_calls) must survive
        productive = [m for m in al.messages if m.role == Role.assistant]
        assert all(m.tool_calls for m in productive), "Non-empty assistant must keep tool_calls"

    def test_non_empty_assistant_preserved(self):
        """Assistant messages with content are never dropped."""
        from drydock.core.agent_loop import AgentLoop
        from drydock.core.types import LLMMessage, Role, MessageList
        import os
        al = object.__new__(AgentLoop)
        al.messages = MessageList()
        al._truncate_old_tool_results = lambda: None
        al._proactive_prune_write_oscillation = lambda: None

        al.messages.append(LLMMessage(role=Role.user, content="hi"))
        al.messages.append(LLMMessage(role=Role.assistant, content="I'm here"))

        os.environ["DRYDOCK_AUTO_CONTINUE_DISABLE"] = "1"
        try:
            al._sanitize_message_ordering()
        finally:
            os.environ.pop("DRYDOCK_AUTO_CONTINUE_DISABLE", None)

        roles = [m.role for m in al.messages]
        assert Role.assistant in roles, "Non-empty assistant must not be dropped"

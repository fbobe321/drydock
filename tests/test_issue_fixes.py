"""Regression tests for GitHub issues #4, #5, #6."""
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

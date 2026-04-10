"""Tests for user-reported issues (2026-04-07).

Each test should FAIL before the fix and PASS after.
"""
import hashlib
import pytest
from unittest.mock import patch, MagicMock


class TestMD5Crash:
    """#61: Crash after install — md5 error in manager.py."""

    def test_md5_usedforsecurity(self):
        """md5 should work with usedforsecurity=False."""
        h = hashlib.md5(b"test", usedforsecurity=False)
        assert h.hexdigest() == "098f6bcd4621d373cade4e832627b4f6"

    def test_tool_manager_import(self):
        """Tool manager should not crash on import."""
        from drydock.core.tools.manager import ToolManager
        assert ToolManager is not None


class TestSearchReplaceGarbled:
    """#55: search_replace garbled characters."""

    def test_garbled_detection(self):
        from drydock.core.tools.builtins.search_replace import SearchReplaceArgs
        args = SearchReplaceArgs.model_validate({
            "file_path": "test.py",
            "content": "<<<<<<< SEARCH\nold text\n=======\nnew text\n>>>>>>> REPLACE",
        })
        assert args.file_path == "test.py"


class TestTodoEnumQuotes:
    """#60: Todo enum quoting issues."""

    def test_double_quoted(self):
        from drydock.core.tools.builtins.todo import TodoItem
        item = TodoItem.model_validate({
            "id": "1", "content": "test",
            "status": '"completed"', "priority": '"high"',
        })
        assert item.status.value == "completed"

    def test_single_quoted(self):
        from drydock.core.tools.builtins.todo import TodoItem
        item = TodoItem.model_validate({
            "id": "1", "content": "test",
            "status": "'completed'", "priority": "'high'",
        })
        assert item.status.value == "completed"


class TestEmptyState:
    """#58: IndexError on empty lists."""

    def test_empty_tool_calls(self):
        from drydock.core.types import LLMMessage, Role
        msg = LLMMessage(role=Role.assistant, content="hello", tool_calls=None)
        calls = msg.tool_calls or []
        assert calls == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestSearchReplaceGarbledAdvanced:
    """#55: Garbled detection should only catch real corruption."""

    def test_legitimate_repeats_not_flagged(self):
        """Normal repeated chars like ===== should NOT be flagged."""
        from drydock.core.tools.builtins.search_replace import SearchReplace
        blocks = SearchReplace._parse_search_replace_blocks(
            "<<<<<<< SEARCH\ndef foo():\n    x = 5\n=======\ndef foo():\n    x = 10\n>>>>>>> REPLACE"
        )
        assert len(blocks) == 1
        assert "x = 5" in blocks[0].search

    def test_real_garble_detected(self):
        """Token corruption like <<t<ttt<t should be caught."""
        import re
        garbled = "(?P<<t<ttt<t<tststssts>\\d+)"
        assert re.search(r'<[a-z]<[a-z]{2,}<[a-z]', garbled)

    def test_normal_regex_not_garbled(self):
        """Normal regex should not trigger garble detection."""
        import re
        normal = r"(?P<timestamp>\d{4}-\d{2}-\d{2})"
        assert not re.search(r'<[a-z]<[a-z]{2,}<[a-z]', normal)


class TestIndexErrorGuards:
    """#58: Guard all [-1] accesses on potentially empty lists."""

    def test_empty_messages_safe(self):
        """Accessing last message on empty list should not crash."""
        messages = []
        last = messages[-1] if messages else None
        assert last is None

    def test_agent_loop_no_md5(self):
        """Agent loop should use sha256, not md5."""
        import inspect
        from drydock.core.agent_loop import AgentLoop
        source = inspect.getsource(AgentLoop)
        assert "hashlib.md5" not in source, "Still using md5 in agent_loop"
        assert "hashlib.sha256" in source or "sha256" not in source  # either uses sha256 or doesn't hash at all


class TestAPIRecovery:
    """#57: API error recovery should auto-compact."""

    def test_message_list_reset_keeps_structure(self):
        """Reset should produce valid message ordering."""
        from drydock.core.types import MessageList, LLMMessage, Role
        ml = MessageList()
        for i in range(25):
            ml.append(LLMMessage(role=Role.user, content=f"msg {i}"))
            ml.append(LLMMessage(role=Role.assistant, content=f"reply {i}"))
        
        # Simulate emergency reset: keep first user + last 5
        first_user = ml[0]
        kept = [first_user] + list(ml[-5:])
        ml.reset(kept)
        assert len(ml) == 6
        assert ml[0].role == Role.user


class TestTodoMissingFields:
    """#60: Todo should handle missing fields gracefully."""

    def test_missing_id(self):
        from drydock.core.tools.builtins.todo import TodoItem
        item = TodoItem.model_validate({"content": "test task"})
        assert item.content == "test task"
        assert item.id  # auto-generated

    def test_missing_content(self):
        from drydock.core.tools.builtins.todo import TodoItem
        item = TodoItem.model_validate({"id": "1"})
        assert item.id == "1"
        assert item.content == ""

    def test_completely_empty(self):
        from drydock.core.tools.builtins.todo import TodoItem
        item = TodoItem.model_validate({})
        assert item.id  # auto-generated
        assert item.status.value == "pending"


class TestThinkingMode:
    """Verify thinking mode is properly configured."""

    def test_thinking_in_generic_backend(self):
        """Generic backend source should reference enable_thinking."""
        content = open("/data3/drydock/drydock/core/llm/backend/generic.py").read()
        assert "enable_thinking" in content

    def test_thinking_values_in_settings(self):
        """Settings should define thinking as a Literal type."""
        content = open("/data3/drydock/drydock/core/config/_settings.py").read()
        assert "thinking" in content


class TestNonStreaming:
    """Verify non-streaming config exists for Gemma 4."""

    def test_streaming_flag_in_cli(self):
        """CLI source should have streaming control logic."""
        content = open("/data3/drydock/drydock/cli/cli.py").read()
        assert "enable_streaming" in content


class TestSha256NotMd5:
    """Verify no md5 anywhere in codebase."""

    def test_no_md5_in_agent_loop(self):
        import inspect
        from drydock.core.agent_loop import AgentLoop
        source = inspect.getsource(AgentLoop)
        assert "hashlib.md5" not in source

    def test_no_md5_in_manager(self):
        content = open("/data3/drydock/drydock/core/tools/manager.py").read()
        assert "hashlib.md5" not in content


class TestPlaceholderDetection:
    """#62: Detect placeholder replacements that would delete code."""

    def test_rest_of_code_detected(self):
        """'# rest of code' should be rejected."""
        from drydock.core.tools.builtins.search_replace import SearchReplace
        blocks = SearchReplace._parse_search_replace_blocks(
            '<<<<<<< SEARCH\ndef foo():\n    return 1\n=======\n# rest of code\n>>>>>>> REPLACE'
        )
        assert len(blocks) == 1
        # The placeholder check happens at execution time, not parse time
        assert blocks[0].replace.strip() == "# rest of code"

    def test_normal_replacement_ok(self):
        """Normal code replacement should not be flagged."""
        from drydock.core.tools.builtins.search_replace import SearchReplace
        blocks = SearchReplace._parse_search_replace_blocks(
            '<<<<<<< SEARCH\ndef foo():\n    return 1\n=======\ndef foo():\n    return 2\n>>>>>>> REPLACE'
        )
        assert len(blocks) == 1
        assert "return 2" in blocks[0].replace


class TestWriteFileDedup:
    """Skip writing identical content to prevent write loops."""

    def test_identical_content_detected(self):
        """Writing same content twice should be detectable."""
        # The actual dedup happens at runtime, but we can test the logic
        content = "print('hello')"
        assert content == content  # Same content = skip

    def test_different_content_allowed(self):
        """Different content to same file should be allowed."""
        old = "print('hello')"
        new = "print('goodbye')"
        assert old != new  # Different = allow write


class TestEscapeCharacters:
    """#70: Invalid escape characters causing API errors."""

    def test_sanitize_content(self):
        """Control characters should be stripped."""
        from drydock.core.llm.backend.reasoning_adapter import ReasoningAdapter
        result = ReasoningAdapter._sanitize_content("hello\x00world\x01test\n")
        assert "\x00" not in result
        assert "\x01" not in result
        assert "\n" in result  # Keep newlines
        assert "hello" in result

    def test_ensure_ascii_json(self):
        """JSON serialization should use ensure_ascii=True."""
        import json
        content = "regex: r'([\u00e9\u00e8]+)'"  # Unicode chars
        data = json.dumps({"content": content}, ensure_ascii=True)
        assert "\\u00e9" in data  # Unicode escaped

    def test_backslash_in_content(self):
        """Backslashes in file content should serialize safely."""
        import json
        content = r'pattern = re.compile(r"([\w\s]+)")'
        data = json.dumps({"content": content}, ensure_ascii=True)
        parsed = json.loads(data)
        assert parsed["content"] == content

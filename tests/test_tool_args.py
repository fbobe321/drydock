"""Test all tools accept the argument formats Gemma 4 actually sends.

Gemma 4 quirks:
- Wraps enum values in extra quotes: "'completed'" instead of "completed"
- Sometimes omits required fields
- Sometimes sends empty {} for all args
- Sends file_path as "path" or vice versa
- Sends content in wrong field for search_replace
"""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock


def try_validate(args_class, data: dict) -> tuple[bool, str]:
    """Try to validate args, return (success, error_message)."""
    try:
        args_class.model_validate(data)
        return True, ""
    except Exception as e:
        return False, str(e)[:200]


class TestTodoArgs:
    """Todo tool argument validation."""

    def setup_method(self):
        from drydock.core.tools.builtins.todo import TodoArgs, TodoItem
        self.TodoArgs = TodoArgs
        self.TodoItem = TodoItem

    def test_empty_args_has_default(self):
        """Empty {} should work — action defaults to 'read'."""
        ok, err = try_validate(self.TodoArgs, {})
        assert ok, f"Empty args failed: {err}"

    def test_quoted_enum_status(self):
        """Gemma 4 sends \"'completed'\" with extra quotes."""
        ok, err = try_validate(self.TodoItem, {
            "id": "1", "content": "test", "status": "'completed'", "priority": "high"
        })
        assert ok, f"Quoted status failed: {err}"

    def test_quoted_enum_priority(self):
        """Gemma 4 sends \"'high'\" with extra quotes."""
        ok, err = try_validate(self.TodoItem, {
            "id": "1", "content": "test", "status": "pending", "priority": "'high'"
        })
        assert ok, f"Quoted priority failed: {err}"

    def test_uppercase_enum(self):
        """Model might send COMPLETED instead of completed."""
        ok, err = try_validate(self.TodoItem, {
            "id": "1", "content": "test", "status": "COMPLETED", "priority": "HIGH"
        })
        assert ok, f"Uppercase enum failed: {err}"


class TestWriteFileArgs:
    """Write file tool argument validation."""

    def setup_method(self):
        from drydock.core.tools.builtins.write_file import WriteFileArgs
        self.WriteFileArgs = WriteFileArgs

    def test_normal_args(self):
        ok, err = try_validate(self.WriteFileArgs, {
            "path": "test.py", "content": "print('hello')"
        })
        assert ok, f"Normal args failed: {err}"

    def test_overwrite_defaults_true(self):
        """Overwrite should default to True."""
        args = self.WriteFileArgs.model_validate({"path": "test.py", "content": "x"})
        assert args.overwrite is True

    def test_empty_content(self):
        """Empty content should be accepted (for __init__.py)."""
        ok, err = try_validate(self.WriteFileArgs, {
            "path": "test.py", "content": ""
        })
        assert ok, f"Empty content failed: {err}"


class TestSearchReplaceArgs:
    """Search replace tool argument validation."""

    def setup_method(self):
        from drydock.core.tools.builtins.search_replace import SearchReplaceArgs
        self.SearchReplaceArgs = SearchReplaceArgs

    def test_normal_args(self):
        ok, err = try_validate(self.SearchReplaceArgs, {
            "file_path": "test.py",
            "content": "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"
        })
        assert ok, f"Normal args failed: {err}"

    def test_missing_file_path_has_default(self):
        """file_path should default to empty string, not crash."""
        ok, err = try_validate(self.SearchReplaceArgs, {
            "content": "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"
        })
        assert ok, f"Missing file_path failed: {err}"

    def test_old_new_string_direct(self):
        """Gemma 4 sometimes sends old_string/new_string directly."""
        ok, err = try_validate(self.SearchReplaceArgs, {
            "file_path": "test.py",
            "old_string": "old text",
            "new_string": "new text"
        })
        assert ok, f"Direct old/new string failed: {err}"

    def test_empty_args(self):
        """Empty args should not crash."""
        ok, err = try_validate(self.SearchReplaceArgs, {})
        assert ok, f"Empty args failed: {err}"


class TestAskUserQuestionArgs:
    """Ask user question tool argument validation."""

    def setup_method(self):
        from drydock.core.tools.builtins.ask_user_question import AskUserQuestionArgs
        self.AskUserQuestionArgs = AskUserQuestionArgs

    def test_empty_args_has_default(self):
        """Empty {} should work — questions has a default."""
        ok, err = try_validate(self.AskUserQuestionArgs, {})
        assert ok, f"Empty args failed: {err}"


class TestReadFileArgs:
    """Read file tool argument validation."""

    def setup_method(self):
        from drydock.core.tools.builtins.read_file import ReadFileArgs
        self.ReadFileArgs = ReadFileArgs

    def test_path_field(self):
        ok, err = try_validate(self.ReadFileArgs, {"path": "test.py"})
        assert ok, f"Path field failed: {err}"

    def test_with_offset_limit(self):
        ok, err = try_validate(self.ReadFileArgs, {
            "path": "test.py", "offset": 10, "limit": 50
        })
        assert ok, f"Offset/limit failed: {err}"


class TestBashArgs:
    """Bash tool argument validation."""

    def setup_method(self):
        from drydock.core.tools.builtins.bash import BashArgs
        self.BashArgs = BashArgs

    def test_command_field(self):
        ok, err = try_validate(self.BashArgs, {"command": "ls -la"})
        assert ok, f"Command field failed: {err}"

    def test_with_timeout(self):
        ok, err = try_validate(self.BashArgs, {"command": "sleep 1", "timeout": 5})
        assert ok, f"Timeout failed: {err}"


class TestGrepArgs:
    """Grep tool argument validation."""

    def setup_method(self):
        from drydock.core.tools.builtins.grep import GrepArgs
        self.GrepArgs = GrepArgs

    def test_pattern_only(self):
        ok, err = try_validate(self.GrepArgs, {"pattern": "def main"})
        assert ok, f"Pattern only failed: {err}"

    def test_with_path(self):
        ok, err = try_validate(self.GrepArgs, {"pattern": "import", "path": "."})
        assert ok, f"With path failed: {err}"


class TestGlobArgs:
    """Glob tool argument validation."""

    def setup_method(self):
        from drydock.core.tools.builtins.glob_tool import GlobArgs
        self.GlobArgs = GlobArgs

    def test_pattern(self):
        ok, err = try_validate(self.GlobArgs, {"pattern": "*.py"})
        assert ok, f"Pattern failed: {err}"


class TestTaskCreateArgs:
    """Task create tool argument validation."""

    def setup_method(self):
        from drydock.core.tools.builtins._task_manager import TaskCreateArgs
        self.TaskCreateArgs = TaskCreateArgs

    def test_normal(self):
        ok, err = try_validate(self.TaskCreateArgs, {"title": "Build the app"})
        assert ok, f"Normal args failed: {err}"

    def test_with_description(self):
        ok, err = try_validate(self.TaskCreateArgs, {
            "title": "Build the app", "description": "Create all files"
        })
        assert ok, f"With description failed: {err}"


class TestSearchReplaceBlockParsing:
    """Test that search_replace parses various block formats."""

    def setup_method(self):
        from drydock.core.tools.builtins.search_replace import SearchReplace
        self.parse = SearchReplace._parse_search_replace_blocks

    def test_standard_format(self):
        blocks = self.parse("<<<<<<< SEARCH\nold text\n=======\nnew text\n>>>>>>> REPLACE")
        assert len(blocks) == 1
        assert blocks[0].search == "old text"
        assert blocks[0].replace == "new text"

    def test_repeated_search_word(self):
        """Gemma 4 sometimes repeats: SEARCH SEARCH SEARCH."""
        blocks = self.parse("<<<<<<<<< SEARCH SEARCH SEARCH\nold\n=======\nnew\n>>>>>>>>> REPLACE REPLACE")
        assert len(blocks) == 1

    def test_json_format(self):
        """Gemma 4 sometimes sends JSON."""
        blocks = self.parse('{"old_string": "old", "new_string": "new"}')
        assert len(blocks) == 1
        assert blocks[0].search == "old"

    def test_separator_format(self):
        """Simple ======= separator."""
        blocks = self.parse("old text\n=======\nnew text")
        assert len(blocks) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

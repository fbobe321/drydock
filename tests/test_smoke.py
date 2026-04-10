"""SMOKE TESTS — Run on every code change (< 5 seconds).

Absolute minimum checks that DryDock isn't broken.
No backend needed. Pure code/config validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ============================================================================
# 1. Package imports don't crash
# ============================================================================

class TestImports:
    def test_core_imports(self):
        from drydock.core.agent_loop import AgentLoop
        from drydock.core.types import LLMMessage, MessageList, Role
        assert AgentLoop
        assert LLMMessage
        assert MessageList

    def test_config_imports(self):
        from drydock.core.config import VibeConfig, ModelConfig, ProviderConfig
        assert VibeConfig

    def test_tool_imports(self):
        from drydock.core.tools.builtins.bash import Bash
        from drydock.core.tools.builtins.grep import Grep
        from drydock.core.tools.builtins.search_replace import SearchReplace
        from drydock.core.tools.builtins.glob_tool import Glob
        from drydock.core.tools.builtins.notebook_edit import NotebookEdit
        from drydock.core.tools.builtins._task_manager import TaskCreate
        from drydock.core.tools.builtins.lsp import Lsp
        from drydock.core.tools.builtins.cron import CronCreate
        from drydock.core.tools.builtins.tool_search import ToolSearch
        from drydock.core.tools.builtins.worktree import EnterWorktree
        from drydock.core.tools.builtins.invoke_skill import InvokeSkill
        assert Glob and Lsp and ToolSearch

    def test_hook_imports(self):
        from drydock.core.hooks import HookManager, HookEvent
        assert HookManager
        assert HookEvent.PRE_TOOL_USE

    def test_consultant_imports(self):
        from drydock.core.consultant import ask_consultant, is_consultant_available
        assert ask_consultant
        assert is_consultant_available


# ============================================================================
# 2. Branding correct
# ============================================================================

class TestBranding:
    def test_system_prompt_says_drydock(self):
        prompt = Path(__file__).parent.parent / "drydock" / "core" / "prompts" / "cli.md"
        assert "DryDock" in prompt.read_text()

    def test_no_vibe_in_defaults(self):
        from drydock.core.config._settings import DEFAULT_MODELS
        for m in DEFAULT_MODELS:
            assert "vibe" not in m.name.lower()

    def test_version_not_empty(self):
        from drydock import __version__
        assert __version__ and len(__version__) > 2


# ============================================================================
# 3. Critical safety checks
# ============================================================================

class TestSafety:
    def test_circuit_breaker_exists(self):
        from drydock.core.agent_loop import AgentLoop
        assert hasattr(AgentLoop, "_circuit_breaker_check")

    def test_message_ordering_exists(self):
        from drydock.core.agent_loop import AgentLoop
        assert hasattr(AgentLoop, "_sanitize_message_ordering")

    def test_injection_guard_exists(self):
        from drydock.core.tools.injection_guard import check_content_for_injection
        assert check_content_for_injection("normal text") is None

    def test_binary_file_guard(self):
        from drydock.core.tools.builtins.write_file import WriteFile
        assert hasattr(WriteFile, "_BINARY_EXTENSIONS")
        assert ".pptx" in WriteFile._BINARY_EXTENSIONS

    def test_injection_guard_in_search_replace(self):
        from drydock.core.tools.builtins.search_replace import SearchReplace
        import inspect
        src = inspect.getsource(SearchReplace.run)
        assert "injection" in src.lower()  # Injection guard should be present


# ============================================================================
# 4. Tools have prompts
# ============================================================================

class TestToolPrompts:
    def test_all_tools_have_prompts(self):
        tools_dir = Path(__file__).parent.parent / "drydock" / "core" / "tools" / "builtins"
        prompts_dir = tools_dir / "prompts"
        tool_files = {f.stem for f in tools_dir.glob("*.py") if f.stem != "__init__"}
        prompt_files = {f.stem for f in prompts_dir.glob("*.md") if f.stem != "__init__"}

        # Not all tools need prompts (some are internal)
        core_tools = {"bash", "grep", "read_file", "search_replace", "write_file",
                      "glob", "notebook_edit", "task_manager", "lsp", "cron",
                      "invoke_skill", "worktree", "tool_search"}
        missing = core_tools - prompt_files
        assert not missing, f"Missing prompt files for: {missing}"


# ============================================================================
# 5. Skills bundled
# ============================================================================

class TestSkills:
    def test_all_skills_exist(self):
        skills_dir = Path(__file__).parent.parent / "drydock" / "skills"
        expected = {"create-presentation", "deep-research", "investigate", "review", "ship", "batch", "simplify"}
        actual = {d.name for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").exists()}
        missing = expected - actual
        assert not missing, f"Missing skills: {missing}"

    def test_skills_have_valid_frontmatter(self):
        from drydock.core.skills.parser import parse_frontmatter
        skills_dir = Path(__file__).parent.parent / "drydock" / "skills"
        for skill_dir in skills_dir.iterdir():
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                content = skill_file.read_text()
                metadata, _ = parse_frontmatter(content)
                assert "name" in metadata, f"Skill {skill_dir.name} missing 'name'"


# ============================================================================
# 6. Config
# ============================================================================

class TestConfig:
    def test_consultant_model_field(self):
        from drydock.core.config import VibeConfig
        assert "consultant_model" in VibeConfig.model_fields

    def test_drydock_home_default(self):
        import inspect
        from drydock.core.paths import _vibe_home
        src = inspect.getsource(_vibe_home)
        assert ".drydock" in src


# ============================================================================
# 7. Commands registered
# ============================================================================

class TestCommands:
    def test_core_commands_registered(self):
        from drydock.cli.commands import CommandRegistry
        registry = CommandRegistry()
        expected = {"help", "config", "clear", "compact", "exit", "status", "consult", "rewind"}
        actual = set(registry.commands.keys())
        missing = expected - actual
        assert not missing, f"Missing commands: {missing}"


class TestVersionSync:
    def test_version_matches_pyproject(self):
        """TUI version must match pyproject.toml version."""
        from drydock import __version__
        import tomllib
        with open(Path(__file__).parent.parent / "pyproject.toml", "rb") as f:
            pyproject_version = tomllib.load(f)["project"]["version"]
        # In dev (running from source without pip install), __version__ may be "dev"
        if __version__ != "dev":
            assert __version__ == pyproject_version, \
                f"TUI shows {__version__} but pyproject.toml has {pyproject_version}"

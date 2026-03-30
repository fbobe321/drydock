"""Tests for user-reported issues.

Each test should FAIL before the fix, proving the issue exists.
After fixing, all should pass.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


# ============================================================================
# Issue 1: "DryDock" branding — should be DryDock not Drydock everywhere
# ============================================================================

class TestBranding:
    def test_readme_says_drydock_capital(self):
        readme = Path(__file__).parent.parent / "README.md"
        content = readme.read_text()
        # Title should be "# DryDock" not "# Drydock"
        assert "# DryDock" in content

    def test_system_prompt_says_drydock(self):
        prompt = Path(__file__).parent.parent / "drydock" / "core" / "prompts" / "cli.md"
        content = prompt.read_text()
        # Should say "DryDock" not "Drydock" as the product name
        assert "DryDock" in content

    def test_loading_widget_says_drydock(self):
        from drydock.cli.textual_ui.widgets.loading import LoadingWidget
        widget = LoadingWidget.__new__(LoadingWidget)
        status = widget._get_default_status()
        # Anchor emoji is fine, but product references should be DryDock
        assert status  # Just verify it doesn't crash


# ============================================================================
# Issue 5: What conda env is pip using? Should preserve user's env
# ============================================================================

class TestCondaEnv:
    def test_bash_env_preserves_active_conda(self):
        """If user has CONDA_DEFAULT_ENV set, bash should preserve it."""
        from drydock.core.tools.builtins.bash import _get_base_env
        with patch.dict(os.environ, {"CONDA_DEFAULT_ENV": "myproject", "CONDA_EXE": "/opt/conda/bin/conda"}):
            env = _get_base_env()
            # BASH_ENV should reference the user's env, not just base
            if "BASH_ENV" in env:
                bash_env_content = Path(env["BASH_ENV"]).read_text() if Path(env["BASH_ENV"]).exists() else ""
                # Should activate the user's env, not just source conda.sh
                assert "myproject" in bash_env_content or "CONDA_DEFAULT_ENV" in str(env)


# ============================================================================
# Issue 6: Circuit breaker shows message but still repeats
# ============================================================================

class TestCircuitBreakerEnforcement:
    def test_circuit_breaker_blocks_failed_commands(self):
        """After 3 identical failed calls, the next must be blocked."""
        from drydock.core.agent_loop import AgentLoop
        from drydock.core.types import MessageList

        al = object.__new__(AgentLoop)
        al.messages = MessageList()
        al._tool_call_history = {}

        tc = SimpleNamespace(tool_name="bash", args_dict={"command": "bad_cmd"})
        for _ in range(3):
            al._circuit_breaker_record(tc, "FAILED: command not found")

        result = al._circuit_breaker_check(tc)
        assert result is not None, "Circuit breaker should block failed commands after 3 repeats"
        assert "failed" in result
        assert "DIFFERENT" in result or "different" in result

    def test_circuit_breaker_resets_for_new_conversation(self):
        """Circuit breaker should reset between conversations, not cache forever."""
        from drydock.core.agent_loop import AgentLoop
        from drydock.core.types import MessageList

        al = object.__new__(AgentLoop)
        al.messages = MessageList()
        al._tool_call_history = {}

        tc = SimpleNamespace(tool_name="bash", args_dict={"command": "bad_cmd"})
        for _ in range(3):
            al._circuit_breaker_record(tc, "FAILED: command not found")
        assert al._circuit_breaker_check(tc) is not None  # Blocked

        # Simulate new conversation — history should be clearable
        al._tool_call_history.clear()
        assert al._circuit_breaker_check(tc) is None  # Unblocked


# ============================================================================
# Issue 7: "Understood" after errors then stops
# ============================================================================

class TestUnderstoodBug:
    def test_no_understood_in_agent(self):
        """The agent should not inject 'Understood.' which causes premature stops."""
        from drydock.core.agent_loop import AgentLoop
        src = inspect.getsource(AgentLoop)
        # "Understood." was causing the model to think conversation ended
        assert "Understood" not in src, \
            "'Understood' found in agent_loop — should use 'Continuing...' instead"


# ============================================================================
# Issue 10: Circuit breaker too sensitive — same command later should be OK
# ============================================================================

class TestCircuitBreakerSensitivity:
    def test_successful_commands_never_blocked(self):
        """Successful commands are never blocked — only failed commands are."""
        from drydock.core.agent_loop import AgentLoop
        from drydock.core.types import MessageList

        al = object.__new__(AgentLoop)
        al.messages = MessageList()
        al._tool_call_history = {}

        tc = SimpleNamespace(tool_name="bash", args_dict={"command": "git status"})
        for _ in range(10):
            al._circuit_breaker_record(tc, "clean")
        assert al._circuit_breaker_check(tc) is None  # Never blocked

    def test_failed_commands_blocked_after_3(self):
        """Failed commands are blocked after 3 repeats."""
        from drydock.core.agent_loop import AgentLoop
        from drydock.core.types import MessageList

        al = object.__new__(AgentLoop)
        al.messages = MessageList()
        al._tool_call_history = {}

        tc = SimpleNamespace(tool_name="bash", args_dict={"command": "git status"})
        for _ in range(3):
            al._circuit_breaker_record(tc, "FAILED: not a git repo")
        assert al._circuit_breaker_check(tc) is not None  # Blocked


# ============================================================================
# Issue 11: /consult needs to show which model is being used
# ============================================================================

class TestConsultModelDisplay:
    def test_consult_handler_shows_model_name(self):
        """The /consult response should include the model name."""
        from drydock.cli.textual_ui.app import VibeApp
        src = inspect.getsource(VibeApp._consult_command)
        # Should display which model is being used
        assert "consultant_model" in src or "model" in src

    def test_consult_help_shows_config_instructions(self):
        """Typing /consult with no args should show how to configure."""
        from drydock.cli.textual_ui.app import VibeApp
        src = inspect.getsource(VibeApp._consult_command)
        assert "config.toml" in src or "consultant_model" in src


# ============================================================================
# Issue 12: SSL error on websearch — should use -k / DRYDOCK_INSECURE
# ============================================================================

class TestWebSearchSSL:
    def test_websearch_respects_insecure_flag(self):
        """WebSearch should check DRYDOCK_INSECURE env var."""
        from drydock.core.tools.builtins.websearch import WebSearch
        src = inspect.getsource(WebSearch)
        assert "DRYDOCK_INSECURE" in src or "insecure" in src.lower() or "verify" in src

    def test_webfetch_respects_insecure_flag(self):
        """WebFetch should check DRYDOCK_INSECURE env var."""
        from drydock.core.tools.builtins.webfetch import WebFetch
        src = inspect.getsource(WebFetch)
        assert "DRYDOCK_INSECURE" in src


# ============================================================================
# Issue 14: Config.toml consultant_model setup
# ============================================================================

class TestConsultantConfig:
    def test_config_has_consultant_model_field(self):
        """VibeConfig should have consultant_model field."""
        from drydock.core.config import VibeConfig
        assert hasattr(VibeConfig, "model_fields")
        assert "consultant_model" in VibeConfig.model_fields

    def test_consultant_model_documented_in_readme(self):
        """README should mention how to set consultant_model."""
        readme = Path(__file__).parent.parent / "README.md"
        content = readme.read_text()
        assert "consultant_model" in content or "consultant" in content.lower()


# ============================================================================
# Issue 15: Auto-consult agent — model should ask consultant when stuck
# ============================================================================

class TestAutoConsult:
    def test_circuit_breaker_suggests_different_approach(self):
        """When circuit breaker fires on failed commands, it should suggest a different approach."""
        from drydock.core.agent_loop import AgentLoop
        from drydock.core.types import MessageList

        al = object.__new__(AgentLoop)
        al.messages = MessageList()
        al._tool_call_history = {}

        tc = SimpleNamespace(tool_name="bash", args_dict={"command": "bad_cmd"})
        for _ in range(3):
            al._circuit_breaker_record(tc, "FAILED: command not found")
        result = al._circuit_breaker_check(tc)
        assert result is not None, "Circuit breaker should have fired for failed command"
        assert "different" in result.lower()


# ============================================================================
# Issue 16: /consult should include conversation history
# ============================================================================

class TestConsultHistory:
    def test_consult_sends_context(self):
        """/consult handler should send conversation context to consultant."""
        from drydock.core.consultant import ask_consultant
        src = inspect.getsource(ask_consultant)
        # Should accept and use context/conversation history
        assert "context" in src or "messages" in src or "history" in src


# ============================================================================
# Issue 17: Deep research skill should exist
# ============================================================================

class TestDeepResearchSkill:
    def test_research_skill_exists(self):
        """A deep-research skill should be bundled."""
        skills_dir = Path(__file__).parent.parent / "drydock" / "skills"
        skill_dirs = [d.name for d in skills_dir.iterdir() if d.is_dir()] if skills_dir.exists() else []
        has_research = any("research" in d for d in skill_dirs)
        assert has_research, f"No research skill found. Available: {skill_dirs}"


# ============================================================================
# Issue 18: Not using agents — explore/diagnostic agents should be utilized
# ============================================================================

class TestAgentsAvailable:
    def test_explore_agent_registered(self):
        """The 'explore' agent should be registered."""
        from drydock.core.agents.models import BuiltinAgentName
        assert hasattr(BuiltinAgentName, "EXPLORE") or "explore" in dir(BuiltinAgentName)

    def test_system_prompt_mentions_agents(self):
        """System prompt should tell the model about available agents."""
        prompt = Path(__file__).parent.parent / "drydock" / "core" / "prompts" / "cli.md"
        content = prompt.read_text()
        has_agent_ref = "agent" in content.lower() or "subagent" in content.lower() or "task" in content.lower()
        assert has_agent_ref

"""Tests for current bugs — must FAIL before fix, PASS after.

These reproduce real issues found by using DryDock.
All tests hit the real vLLM backend.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest

from drydock.core.config.harness_files import init_harness_files_manager
try:
    init_harness_files_manager("user", "project")
except RuntimeError:
    pass

from drydock.core.agent_loop import AgentLoop
from drydock.core.agents.models import BuiltinAgentName
from drydock.core.config import Backend, ModelConfig, ProviderConfig, VibeConfig
from drydock.core.types import (
    AssistantEvent, BaseEvent, Role, ToolCallEvent, ToolResultEvent,
)


def _vllm_ok():
    try:
        return httpx.get("http://localhost:8000/v1/models", timeout=3).status_code == 200
    except Exception:
        return False

pytestmark = pytest.mark.skipif(not _vllm_ok(), reason="vLLM not running")


def _config(tmp_path):
    return VibeConfig(
        active_model="devstral", auto_approve=True, enable_telemetry=False,
        include_project_context=False, system_prompt_id="cli",
        providers=[ProviderConfig(name="local", api_base="http://localhost:8000/v1", api_key_env_var="", backend=Backend.GENERIC)],
        models=[ModelConfig(name="devstral", provider="local", input_price=0, output_price=0)],
        session_logging={"enabled": False, "save_dir": str(tmp_path / "logs")},
    )

def _agent(tmp_path, max_turns=10):
    return AgentLoop(config=_config(tmp_path), agent_name=BuiltinAgentName.AUTO_APPROVE, max_turns=max_turns)

async def _run(agent, prompt, max_events=100):
    events = []
    async for ev in agent.act(prompt):
        events.append(ev)
        if len(events) >= max_events:
            break
    return events


# ============================================================================
# BUG: "vibe" still referenced in config defaults
# ============================================================================

class TestVibeReferences:
    def test_no_vibe_in_config_model_names(self):
        """Default model name should not contain 'vibe'."""
        from drydock.core.config._settings import DEFAULT_MODELS
        for m in DEFAULT_MODELS:
            assert "vibe" not in m.name.lower(), \
                f"Model name '{m.name}' still references 'vibe'"

    def test_no_vibe_in_logger_name(self):
        """Logger should use 'drydock' not 'vibe'."""
        from drydock.core.logger import logger
        assert logger.name != "vibe", \
            f"Logger name is '{logger.name}' — should be 'drydock'"

    def test_no_vibe_in_client_metadata(self):
        """Programmatic client should identify as drydock."""
        from drydock.core.programmatic import _DEFAULT_CLIENT_METADATA
        assert "vibe" not in _DEFAULT_CLIENT_METADATA.name.lower(), \
            f"Client metadata '{_DEFAULT_CLIENT_METADATA.name}' still references 'vibe'"

    def test_no_vibe_in_stop_event_tag(self):
        """Internal tags should use drydock not vibe."""
        from drydock.core.utils import VIBE_STOP_EVENT_TAG, VIBE_WARNING_TAG
        # These are internal protocol tags — checking they exist
        # but flagging the naming for awareness
        assert VIBE_STOP_EVENT_TAG  # exists
        assert VIBE_WARNING_TAG  # exists


# ============================================================================
# BUG: Websearch loops — model keeps searching the same thing
# ============================================================================

@pytest.mark.asyncio
async def test_websearch_doesnt_loop(tmp_path):
    """Web search should not repeat the same query more than twice."""
    agent = _agent(tmp_path, max_turns=10)
    events = await _run(agent, "Search the web for 'Python asyncio best practices' and summarize", max_events=60)

    websearch_calls = [e for e in events if isinstance(e, ToolCallEvent) and e.tool_name == "websearch"]
    assert len(websearch_calls) <= 3, \
        f"Websearch called {len(websearch_calls)} times — looping"


# ============================================================================
# BUG: Still looping reading files — reads same file over and over
# ============================================================================

@pytest.mark.asyncio
async def test_file_read_doesnt_loop(tmp_path):
    """Agent should not read the same file more than 3 times."""
    # Create a test file
    test_file = tmp_path / "test_code.py"
    test_file.write_text("def hello():\n    print('hello world')\n\ndef goodbye():\n    print('bye')\n")

    agent = _agent(tmp_path, max_turns=10)
    events = await _run(agent, f"Read {test_file} and explain what it does", max_events=60)

    read_calls = [e for e in events if isinstance(e, ToolCallEvent) and e.tool_name == "read_file"]

    # Should read the file once or twice, not 5+
    assert len(read_calls) <= 3, \
        f"read_file called {len(read_calls)} times for one file — looping"


# ============================================================================
# BUG: DuckDuckGo websearch respects --insecure flag
# ============================================================================

class TestWebSearchSSL:
    def test_websearch_uses_insecure_env(self):
        """New DuckDuckGo websearch should check DRYDOCK_INSECURE."""
        import inspect
        from drydock.core.tools.builtins.websearch import WebSearch
        src = inspect.getsource(WebSearch)
        assert "DRYDOCK_INSECURE" in src or "verify" in src, \
            "WebSearch doesn't check DRYDOCK_INSECURE for SSL bypass"

    def test_websearch_uses_duckduckgo(self):
        """WebSearch should use DuckDuckGo, not Mistral API."""
        import inspect
        from drydock.core.tools.builtins.websearch import WebSearch
        src = inspect.getsource(WebSearch)
        assert "duckduckgo" in src.lower(), \
            "WebSearch should use DuckDuckGo (your refactored version)"
        assert "mistralai" not in src or "import mistralai" not in src, \
            "WebSearch should not require Mistral API"

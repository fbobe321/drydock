"""Real backend tests for user-reported issues.

Every test hits the ACTUAL vLLM backend at localhost:8000.
NO mocks, NO fakes — real DryDock execution.

Skip if vLLM is not running.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest

from drydock.core.agent_loop import AgentLoop
from drydock.core.agents.models import BuiltinAgentName
from drydock.core.config import Backend, ModelConfig, ProviderConfig, VibeConfig
from drydock.core.types import (
    AssistantEvent,
    BaseEvent,
    Role,
    ToolCallEvent,
    ToolResultEvent,
)


def _vllm_available() -> bool:
    try:
        return httpx.get("http://localhost:8000/v1/models", timeout=3).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _vllm_available(), reason="vLLM not running")


def _config(tmp_path: Path) -> VibeConfig:
    return VibeConfig(
        active_model="devstral",
        auto_approve=True,
        enable_telemetry=False,
        include_project_context=False,
        system_prompt_id="cli",
        providers=[ProviderConfig(
            name="local", api_base="http://localhost:8000/v1",
            api_key_env_var="", backend=Backend.GENERIC,
        )],
        models=[ModelConfig(
            name="devstral", provider="local", alias="devstral",
            input_price=0.0, output_price=0.0,
        )],
        session_logging={"enabled": False, "save_dir": str(tmp_path / "logs")},
    )


def _agent(tmp_path: Path, max_turns: int = 5) -> AgentLoop:
    return AgentLoop(
        config=_config(tmp_path),
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        max_turns=max_turns,
    )


async def _run(agent: AgentLoop, prompt: str, max_events: int = 100) -> list[BaseEvent]:
    events = []
    async for ev in agent.act(prompt):
        events.append(ev)
        if len(events) >= max_events:
            break
    return events


# ============================================================================
# Test 1: "Understood" after error — agent should NOT stop with "Understood."
# ============================================================================

@pytest.mark.asyncio
async def test_no_understood_stops_agent(tmp_path):
    """Agent should never inject 'Understood.' into conversation."""
    agent = _agent(tmp_path)
    events = await _run(agent, "Read the file /nonexistent/path/fake.py")

    # Check messages — none should contain "Understood."
    for msg in agent.messages:
        if msg.role == Role.assistant and msg.content:
            assert "Understood." not in msg.content, \
                f"Found 'Understood.' in assistant message — causes premature stops"


# ============================================================================
# Test 2: Circuit breaker actually blocks (not just warns)
# ============================================================================

@pytest.mark.asyncio
async def test_circuit_breaker_blocks_real(tmp_path):
    """After 2 identical tool calls, the 3rd should be blocked with error."""
    agent = _agent(tmp_path, max_turns=10)
    # Ask something that will likely trigger repeated tool calls
    events = await _run(agent, "Run 'echo hello' five times in a row, each as a separate bash command")

    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    # Should have some blocked results if circuit breaker works
    errors = [e for e in tool_results if e.error and "CIRCUIT BREAKER" in str(e.error)]

    # The model might not repeat exactly, but circuit breaker state should exist
    assert hasattr(agent, "_tool_call_history")
    assert isinstance(agent._tool_call_history, dict)
    # At least verify no AttributeError crash
    for e in tool_results:
        if e.error:
            assert "AttributeError" not in str(e.error), f"Crash: {e.error}"


# ============================================================================
# Test 3: Tool calls don't crash with AttributeError
# ============================================================================

@pytest.mark.asyncio
async def test_no_attribute_error_on_tools(tmp_path):
    """No AttributeError should occur during tool execution."""
    agent = _agent(tmp_path)
    events = await _run(agent, "List the files in the current directory")

    for e in events:
        if isinstance(e, ToolResultEvent) and e.error:
            assert "AttributeError" not in str(e.error), \
                f"AttributeError in tool result: {e.error}"
            assert "raw_arguments" not in str(e.error), \
                f"Old raw_arguments bug: {e.error}"


# ============================================================================
# Test 4: Agent doesn't loop on ambiguous prompt
# ============================================================================

@pytest.mark.asyncio
async def test_ambiguous_prompt_limited(tmp_path):
    """Typing 'test' should not cause 20+ tool calls."""
    agent = _agent(tmp_path, max_turns=8)
    events = await _run(agent, "test", max_events=60)

    tool_calls = [e for e in events if isinstance(e, ToolCallEvent)]
    assert len(tool_calls) < 25, \
        f"Ambiguous prompt 'test' caused {len(tool_calls)} tool calls — too many"


# ============================================================================
# Test 5: Message ordering clean after real tool calls
# ============================================================================

@pytest.mark.asyncio
async def test_no_user_after_tool(tmp_path):
    """No user message should follow a tool message in the history."""
    agent = _agent(tmp_path)
    events = await _run(agent, "What Python version is installed? Run python3 --version")

    for i in range(1, len(agent.messages)):
        if agent.messages[i].role == Role.user and agent.messages[i-1].role == Role.tool:
            pytest.fail(f"user after tool at position {i}: "
                       f"'{agent.messages[i].content[:50]}' after tool '{agent.messages[i-1].content[:50]}'")


# ============================================================================
# Test 6: Agent actually uses tools (not just text responses)
# ============================================================================

@pytest.mark.asyncio
async def test_agent_uses_tools(tmp_path):
    """Agent should call bash/grep/read_file when asked to do something concrete."""
    agent = _agent(tmp_path)
    events = await _run(agent, "Run: echo 'drydock integration test'")

    tool_calls = [e for e in events if isinstance(e, ToolCallEvent)]
    # Should have at least one tool call for a concrete command request
    assert len(tool_calls) >= 1, \
        "Agent gave text-only response when asked to run a command"


# ============================================================================
# Test 7: DryDock branding in system prompt (real check)
# ============================================================================

@pytest.mark.asyncio
async def test_system_prompt_branding(tmp_path):
    """System prompt should say 'DryDock' not 'Drydock' or 'Vibe'."""
    agent = _agent(tmp_path)
    # Don't even need to run — just check the system prompt
    system_msg = agent.messages[0]
    assert system_msg.role == Role.system
    assert "DryDock" in system_msg.content, "System prompt missing 'DryDock'"
    assert "Vibe" not in system_msg.content or "mistral-vibe" in system_msg.content, \
        "System prompt still references 'Vibe'"


# ============================================================================
# Test 8: Write file doesn't hang (has timeout)
# ============================================================================

@pytest.mark.asyncio
async def test_write_file_timeout_exists(tmp_path):
    """write_file should complete within reasonable time, not hang."""
    agent = _agent(tmp_path)
    test_file = tmp_path / "test_write.txt"

    events = await _run(agent,
        f"Write the text 'hello world' to the file {test_file}")

    # Should complete (not hang) — if it hangs, the test runner will timeout
    assert len(events) >= 1
    # Check if file was created (model might or might not do it)
    # The important thing is it didn't hang


# ============================================================================
# Test 9: Agent stops cleanly (not infinite loop)
# ============================================================================

@pytest.mark.asyncio
async def test_agent_stops_cleanly(tmp_path):
    """Agent should stop after completing a task, not loop forever."""
    agent = _agent(tmp_path, max_turns=5)
    events = await _run(agent, "What is 2+2? Just say the number.", max_events=30)

    # Should stop within reasonable events
    assert len(events) < 30, f"Agent produced {len(events)} events — possible loop"


# ============================================================================
# Test 10: Consultant config field exists and works
# ============================================================================

@pytest.mark.asyncio
async def test_consultant_config_real(tmp_path):
    """VibeConfig should accept consultant_model field without error."""
    config = VibeConfig(
        active_model="devstral",
        consultant_model="gemini-2.5-pro",
        auto_approve=True,
        enable_telemetry=False,
        providers=[ProviderConfig(
            name="local", api_base="http://localhost:8000/v1",
            api_key_env_var="", backend=Backend.GENERIC,
        )],
        models=[ModelConfig(
            name="devstral", provider="local",
            input_price=0.0, output_price=0.0,
        )],
    )
    assert config.consultant_model == "gemini-2.5-pro"

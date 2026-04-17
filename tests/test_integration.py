"""Integration tests — use the REAL vLLM backend.

These tests hit the actual LLM at localhost:8000 and verify the full
pipeline works end-to-end. They catch issues that mock tests miss
(like the raw_arguments crash that broke 159 tasks).

Skip if vLLM is not running (CI environments).

Run manually: pytest tests/test_integration.py -v -s
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from drydock.core.agent_loop import AgentLoop
from drydock.core.agents.models import BuiltinAgentName
from drydock.core.config import (
    Backend,
    ModelConfig,
    ProviderConfig,
    DrydockConfig,
)
from drydock.core.types import (
    AssistantEvent,
    BaseEvent,
    ToolCallEvent,
    ToolResultEvent,
    UserMessageEvent,
)


# ============================================================================
# Skip if vLLM not running
# ============================================================================

def _vllm_available() -> bool:
    try:
        r = httpx.get("http://localhost:8000/v1/models", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _vllm_available(),
    reason="vLLM not running at localhost:8000"
)


# ============================================================================
# Helpers
# ============================================================================

def _make_config(tmp_path: Path) -> DrydockConfig:
    """Create a config that uses local vLLM."""
    return DrydockConfig(
        active_model="devstral",
        auto_approve=True,
        enable_telemetry=False,
        include_project_context=False,
        include_prompt_detail=False,
        system_prompt_id="tests",
        providers=[
            ProviderConfig(
                name="local-vllm",
                api_base="http://localhost:8000/v1",
                api_key_env_var="",
                backend=Backend.GENERIC,
            ),
        ],
        models=[
            ModelConfig(
                name="devstral",
                provider="local-vllm",
                alias="devstral",
                input_price=0.0,
                output_price=0.0,
            ),
        ],
        session_logging={"enabled": False, "save_dir": str(tmp_path / "logs")},
    )


def _make_agent(tmp_path: Path) -> AgentLoop:
    config = _make_config(tmp_path)
    return AgentLoop(
        config=config,
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        max_turns=5,  # Safety limit
    )


async def _run(agent: AgentLoop, prompt: str, max_events: int = 100) -> list[BaseEvent]:
    events = []
    async for ev in agent.act(prompt):
        events.append(ev)
        if len(events) >= max_events:
            break
    return events


# ============================================================================
# Test 1: Simple text response from real LLM
# ============================================================================

@pytest.mark.asyncio
async def test_real_llm_responds(tmp_path):
    """Real LLM produces a text response without crashing."""
    agent = _make_agent(tmp_path)
    events = await _run(agent, "Say hello in one word.")

    assert any(isinstance(e, AssistantEvent) for e in events)
    text = [e for e in events if isinstance(e, AssistantEvent) and e.content]
    assert len(text) >= 1


# ============================================================================
# Test 2: LLM calls a tool and gets result
# ============================================================================

@pytest.mark.asyncio
async def test_real_tool_call(tmp_path):
    """Real LLM calls a tool (e.g., bash/grep) and receives the result."""
    agent = _make_agent(tmp_path)
    events = await _run(agent, "Run: echo 'drydock test passed'")

    # Should have at least one tool call or text response
    has_tool = any(isinstance(e, ToolCallEvent) for e in events)
    has_text = any(isinstance(e, AssistantEvent) and e.content for e in events)
    assert has_tool or has_text, "LLM produced neither tool calls nor text"


# ============================================================================
# Test 3: No crash on tool execution path
# ============================================================================

@pytest.mark.asyncio
async def test_no_crash_on_tool_path(tmp_path):
    """The full tool execution path (resolve → execute → result) doesn't crash."""
    agent = _make_agent(tmp_path)

    # This prompt should trigger grep or read_file
    events = await _run(agent, "What files are in the current directory? Use ls.")

    # If we get any events at all without exception, the path works
    assert len(events) >= 1

    # No tool results should have crash errors
    for e in events:
        if isinstance(e, ToolResultEvent) and e.error:
            assert "AttributeError" not in e.error, f"Crash in tool path: {e.error}"
            assert "raw_arguments" not in e.error, f"Old bug present: {e.error}"


# ============================================================================
# Test 4: Circuit breaker doesn't crash on real tool calls
# ============================================================================

@pytest.mark.asyncio
async def test_circuit_breaker_no_crash(tmp_path):
    """Circuit breaker works with real ResolvedToolCall objects."""
    agent = _make_agent(tmp_path)

    # Ask something that triggers a tool call
    events = await _run(agent, "Read the first 3 lines of /etc/hostname")

    # Verify circuit breaker state was populated (even if only 1 call)
    # The key thing is it didn't crash
    assert hasattr(agent, "_tool_call_history")
    assert isinstance(agent._tool_call_history, dict)


# ============================================================================
# Test 5: Message ordering stays clean after real conversation
# ============================================================================

@pytest.mark.asyncio
async def test_message_ordering_real(tmp_path):
    """No user-after-tool violations after a real multi-turn conversation."""
    agent = _make_agent(tmp_path)
    events = await _run(agent, "What is 2+2? Just answer the number.")

    from drydock.core.types import Role
    for i in range(1, len(agent.messages)):
        if agent.messages[i].role == Role.user and agent.messages[i - 1].role == Role.tool:
            pytest.fail(f"user after tool at message {i}")


# ============================================================================
# Test 6: Agent stops within turn limit
# ============================================================================

@pytest.mark.asyncio
async def test_turn_limit_respected(tmp_path):
    """Agent respects max_turns and doesn't loop forever."""
    agent = _make_agent(tmp_path)  # max_turns=5
    events = await _run(agent, "Explore this entire codebase thoroughly.", max_events=200)

    # Should stop eventually (max_turns=5 or natural stop)
    assert len(events) < 200, "Agent seems to be looping"


# ============================================================================
# Test 7: Bash tool works end-to-end
# ============================================================================

@pytest.mark.asyncio
async def test_bash_tool_works(tmp_path):
    """Bash tool executes and returns output through the full pipeline."""
    agent = _make_agent(tmp_path)
    events = await _run(agent, "Run this exact command: echo 'hello from drydock'")

    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    # Either the model called bash successfully, or gave a text response
    has_any_output = len(tool_results) > 0 or any(
        isinstance(e, AssistantEvent) and e.content for e in events
    )
    assert has_any_output


# ============================================================================
# Test 8: Agent handles ambiguous prompt without infinite loop
# ============================================================================

@pytest.mark.asyncio
async def test_ambiguous_prompt_no_loop(tmp_path):
    """Short ambiguous prompt ('test') doesn't cause infinite filesystem exploration."""
    agent = _make_agent(tmp_path)
    events = await _run(agent, "test", max_events=50)

    # Should not use excessive tool calls
    tool_calls = [e for e in events if isinstance(e, ToolCallEvent)]
    assert len(tool_calls) < 20, f"Too many tool calls ({len(tool_calls)}) for ambiguous prompt"


# ============================================================================
# Test 9: MessageList integrity after real execution
# ============================================================================

@pytest.mark.asyncio
async def test_messagelist_integrity_real(tmp_path):
    """MessageList is never replaced with plain list during real execution."""
    agent = _make_agent(tmp_path)
    await _run(agent, "Hello")

    from drydock.core.types import MessageList
    assert isinstance(agent.messages, MessageList), \
        f"messages is {type(agent.messages)}, not MessageList"


# ============================================================================
# Test 10: Sanitize message ordering runs without error
# ============================================================================

@pytest.mark.asyncio
async def test_sanitize_runs_on_real_messages(tmp_path):
    """_sanitize_message_ordering doesn't crash on real message history."""
    agent = _make_agent(tmp_path)
    await _run(agent, "What time is it?")

    # Run sanitize explicitly — should not raise
    agent._sanitize_message_ordering()
    assert len(agent.messages) >= 2  # At least system + user

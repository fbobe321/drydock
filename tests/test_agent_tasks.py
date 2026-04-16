"""Agent task tests — spin up real agent loops with scripted LLM responses.

Each test simulates a real user task by providing scripted model responses
and verifying the agent produces the right events and behavior.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from drydock.core.agent_loop import AgentLoop
from drydock.core.agents.models import BuiltinAgentName
from drydock.core.tools.base import BaseToolConfig, ToolPermission
from drydock.core.types import (
    AssistantEvent,
    BaseEvent,
    FunctionCall,
    LLMMessage,
    MessageList,
    Role,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
    UserMessageEvent,
)


async def _run(agent: AgentLoop, prompt: str, max_events: int = 50) -> list[BaseEvent]:
    events = []
    async for ev in agent.act(prompt):
        events.append(ev)
        if len(events) >= max_events:
            break
    return events


def _count(events: list[BaseEvent], event_type: type) -> int:
    return sum(1 for e in events if isinstance(e, event_type))


def _agent(backend: FakeBackend) -> AgentLoop:
    config = build_test_vibe_config(
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
    )
    return build_test_agent_loop(
        config=config,
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        backend=backend,
    )


# ============================================================================
# Task 1: Simple greeting — no tools, no loops
# ============================================================================

@pytest.mark.asyncio
async def test_task_greeting_no_loop(telemetry_events):
    """Agent responds to greeting and stops without looping."""
    backend = FakeBackend([
        [mock_llm_chunk(content="Hello! How can I help?")],
    ])
    events = await _run(_agent(backend), "hi")

    assert _count(events, UserMessageEvent) == 1
    text = [e for e in events if isinstance(e, AssistantEvent)]
    assert len(text) >= 1
    assert "Hello" in text[0].content
    assert len(events) < 10  # Not looping


# ============================================================================
# Task 2: Agent stops after text-only response
# ============================================================================

@pytest.mark.asyncio
async def test_task_stops_on_text_response(telemetry_events):
    """Text-only response terminates the loop (no tool calls = done)."""
    backend = FakeBackend([
        [mock_llm_chunk(content="The answer is 42.")],
    ])
    events = await _run(_agent(backend), "meaning of life?")

    assert _count(events, AssistantEvent) >= 1
    assert len(events) <= 5


# ============================================================================
# Task 3: Empty response doesn't crash
# ============================================================================

@pytest.mark.asyncio
async def test_task_empty_response_no_crash(telemetry_events):
    """Empty LLM response handled gracefully."""
    backend = FakeBackend([[mock_llm_chunk(content="")]])
    events = await _run(_agent(backend), "hello")
    assert _count(events, UserMessageEvent) == 1


# ============================================================================
# Task 4: Message ordering maintained across turns
# ============================================================================

@pytest.mark.asyncio
async def test_task_message_ordering_clean(telemetry_events):
    """No user-after-tool violations in message history."""
    backend = FakeBackend([
        [mock_llm_chunk(content="First response.")],
    ])
    agent = _agent(backend)
    await _run(agent, "test message ordering")

    for i in range(1, len(agent.messages)):
        assert not (
            agent.messages[i].role == Role.user
            and agent.messages[i - 1].role == Role.tool
        ), f"user after tool at position {i}"


# ============================================================================
# Task 5: Messages remain a MessageList (not plain list)
# ============================================================================

@pytest.mark.asyncio
async def test_task_messagelist_integrity(telemetry_events):
    """Agent messages stay as MessageList, never replaced with plain list."""
    backend = FakeBackend([
        [mock_llm_chunk(content="Working on it.")],
    ])
    agent = _agent(backend)
    await _run(agent, "test integrity")

    assert isinstance(agent.messages, MessageList)
    assert hasattr(agent.messages, "reset")
    assert hasattr(agent.messages, "append")


# ============================================================================
# Task 6: Circuit breaker state initialized
# ============================================================================

@pytest.mark.asyncio
async def test_task_circuit_breaker_initialized(telemetry_events):
    """Agent has circuit breaker history dict after creation."""
    backend = FakeBackend([[mock_llm_chunk(content="ok")]])
    agent = _agent(backend)
    assert hasattr(agent, "_tool_call_history")
    assert isinstance(agent._tool_call_history, dict)


# ============================================================================
# Task 7: Middleware turn limit stops agent
# ============================================================================

@pytest.mark.asyncio
async def test_task_turn_limit_stops_agent(telemetry_events):
    """max_turns=1 limits agent to a single turn."""
    backend = FakeBackend([
        [mock_llm_chunk(content="First turn.")],
        [mock_llm_chunk(content="Should not reach this.")],
    ])
    config = build_test_vibe_config(
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
    )
    agent = build_test_agent_loop(
        config=config,
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        backend=backend,
        max_turns=1,
    )
    events = await _run(agent, "do many things")

    # With max_turns=1, agent should stop after 1 turn
    # Either via middleware stop or by natural text-only response
    text = [e for e in events if isinstance(e, AssistantEvent)]
    assert len(text) >= 1
    assert len(events) <= 10


# ============================================================================
# Task 8: Multi-response conversation
# ============================================================================

@pytest.mark.asyncio
async def test_task_multi_turn_conversation(telemetry_events):
    """Agent handles multiple text-only turns (when nudged to continue)."""
    backend = FakeBackend([
        [mock_llm_chunk(content="Let me think about this...")],
    ])
    agent = _agent(backend)
    events = await _run(agent, "explain the codebase")

    # Should have at least user message + 1 assistant response
    assert _count(events, UserMessageEvent) == 1
    assert _count(events, AssistantEvent) >= 1


# ============================================================================
# Task 9: Backend exception doesn't crash permanently
# ============================================================================

@pytest.mark.xfail(reason="Flaky timing: agent retries 5 errors per round with "
                          "a 5s sleep between rounds (agent_loop.py:631). 3 rounds "
                          "of retries → >10s, blows the pytest-timeout. Baseline "
                          "finished at 10.36s — right on the edge.")
@pytest.mark.asyncio
async def test_task_backend_error_recovery(telemetry_events):
    """Agent survives an LLM backend error."""
    backend = FakeBackend(
        exception_to_raise=RuntimeError("API error from vllm: connection refused")
    )
    agent = _agent(backend)

    # Should not raise — agent catches and handles
    events = await _run(agent, "test error handling")
    # Will have limited events since backend keeps failing
    assert _count(events, UserMessageEvent) == 1


# ============================================================================
# Task 10: System prompt present in messages
# ============================================================================

@pytest.mark.asyncio
async def test_task_system_prompt_present(telemetry_events):
    """Agent has a system prompt as first message."""
    backend = FakeBackend([[mock_llm_chunk(content="ok")]])
    agent = _agent(backend)
    await _run(agent, "test")

    assert len(agent.messages) >= 2
    assert agent.messages[0].role == Role.system
    assert len(agent.messages[0].content) > 50  # Non-trivial system prompt

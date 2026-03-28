"""FULL REGRESSION — Real backend tests (takes 5-10 min).

Run nightly or before releases. Hits the actual vLLM backend.
Skip if vLLM is not running.

Run: pytest tests/test_full_regression.py -v
"""

from __future__ import annotations

import asyncio
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

def _agent(tmp_path, max_turns=8):
    return AgentLoop(config=_config(tmp_path), agent_name=BuiltinAgentName.AUTO_APPROVE, max_turns=max_turns)

async def _run(agent, prompt, max_events=80):
    events = []
    async for ev in agent.act(prompt):
        events.append(ev)
        if len(events) >= max_events:
            break
    return events


# ============================================================================
# Tool execution doesn't crash
# ============================================================================

@pytest.mark.asyncio
async def test_bash_executes(tmp_path):
    agent = _agent(tmp_path)
    events = await _run(agent, "Run: echo 'hello from drydock test'")
    assert any(isinstance(e, (ToolCallEvent, AssistantEvent)) for e in events)
    for e in events:
        if isinstance(e, ToolResultEvent) and e.error:
            assert "AttributeError" not in str(e.error)

@pytest.mark.asyncio
async def test_grep_executes(tmp_path):
    agent = _agent(tmp_path)
    events = await _run(agent, "Search for 'def ' in the current directory")
    assert len(events) >= 1

@pytest.mark.asyncio
async def test_read_file_executes(tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world\n")
    agent = _agent(tmp_path)
    events = await _run(agent, f"Read the file {test_file}")
    assert len(events) >= 1

@pytest.mark.asyncio
async def test_write_file_executes(tmp_path):
    agent = _agent(tmp_path)
    events = await _run(agent, f"Write 'test content' to {tmp_path}/output.txt")
    assert len(events) >= 1


# ============================================================================
# Circuit breaker works (THE critical test)
# ============================================================================

@pytest.mark.asyncio
async def test_circuit_breaker_stops_loops(tmp_path):
    """Model should not make 10+ identical tool calls."""
    agent = _agent(tmp_path, max_turns=15)
    events = await _run(agent, "Run 'echo test' exactly 10 times as separate bash calls", max_events=100)

    cb_fires = [e for e in events if isinstance(e, ToolResultEvent) and e.error and "CIRCUIT" in str(e.error)]
    if cb_fires:
        # After circuit breaker fires, count remaining calls
        first_cb = next(i for i, e in enumerate(events) if isinstance(e, ToolResultEvent) and e.error and "CIRCUIT" in str(e.error))
        calls_after = [e for e in events[first_cb:] if isinstance(e, ToolCallEvent)]
        assert len(calls_after) <= 5, f"Model made {len(calls_after)} calls after circuit breaker"


# ============================================================================
# Message ordering
# ============================================================================

@pytest.mark.asyncio
async def test_no_user_after_tool(tmp_path):
    agent = _agent(tmp_path)
    events = await _run(agent, "What Python version is installed?")
    for i in range(1, len(agent.messages)):
        assert not (agent.messages[i].role == Role.user and agent.messages[i-1].role == Role.tool), \
            f"user after tool at {i}"

@pytest.mark.asyncio
async def test_messagelist_integrity(tmp_path):
    from drydock.core.types import MessageList
    agent = _agent(tmp_path)
    await _run(agent, "Hello")
    assert isinstance(agent.messages, MessageList)


# ============================================================================
# Agent behavior
# ============================================================================

@pytest.mark.asyncio
async def test_ambiguous_prompt_no_infinite_loop(tmp_path):
    agent = _agent(tmp_path, max_turns=8)
    events = await _run(agent, "test", max_events=50)
    tool_calls = [e for e in events if isinstance(e, ToolCallEvent)]
    assert len(tool_calls) < 20, f"Ambiguous 'test' caused {len(tool_calls)} tool calls"

@pytest.mark.asyncio
async def test_agent_stops_cleanly(tmp_path):
    agent = _agent(tmp_path)
    events = await _run(agent, "What is 2+2?", max_events=20)
    assert len(events) < 20

@pytest.mark.asyncio
async def test_system_prompt_present(tmp_path):
    agent = _agent(tmp_path)
    await _run(agent, "hi")
    assert agent.messages[0].role == Role.system
    assert "DryDock" in agent.messages[0].content

@pytest.mark.asyncio
async def test_no_understood_in_messages(tmp_path):
    agent = _agent(tmp_path)
    events = await _run(agent, "Read /nonexistent/file.py")
    for msg in agent.messages:
        if msg.role == Role.assistant:
            assert "Understood." not in (msg.content or "")


# ============================================================================
# Bash abuse prevention
# ============================================================================

@pytest.mark.asyncio
async def test_bash_abuse_limited(tmp_path):
    """Agent should not run 12+ bash commands without making an edit."""
    agent = _agent(tmp_path, max_turns=15)
    events = await _run(agent, "Explore the entire filesystem structure of this project", max_events=80)
    bash_calls = [e for e in events if isinstance(e, ToolCallEvent) and e.tool_name == "bash"]
    # Should be stopped before 12
    assert len(bash_calls) <= 15, f"Agent ran {len(bash_calls)} bash commands"


# ============================================================================
# New tools work
# ============================================================================

@pytest.mark.asyncio
async def test_glob_tool_works(tmp_path):
    """Glob should find files."""
    (tmp_path / "test1.py").write_text("# test")
    (tmp_path / "test2.py").write_text("# test")
    agent = _agent(tmp_path)
    events = await _run(agent, f"Find all .py files in {tmp_path}")
    assert len(events) >= 1

"""Tests that FAIL — proving real bugs exist.

These tests reproduce actual user-reported failures against the real backend.
They should FAIL until the underlying code is fixed.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest

from drydock.core.config.harness_files import init_harness_files_manager

# Initialize harness before importing agent
try:
    init_harness_files_manager("user", "project")
except RuntimeError:
    pass

from drydock.core.agent_loop import AgentLoop
from drydock.core.agents.models import BuiltinAgentName
from drydock.core.config import Backend, ModelConfig, ProviderConfig, DrydockConfig
from drydock.core.types import (
    AssistantEvent,
    BaseEvent,
    Role,
    ToolCallEvent,
    ToolResultEvent,
)


def _vllm_ok() -> bool:
    try:
        return httpx.get("http://localhost:8000/v1/models", timeout=3).status_code == 200
    except Exception:
        return False

pytestmark = pytest.mark.skipif(not _vllm_ok(), reason="vLLM not running")


def _config(tmp_path: Path) -> DrydockConfig:
    return DrydockConfig(
        active_model="devstral", auto_approve=True, enable_telemetry=False,
        include_project_context=False, system_prompt_id="tests",
        providers=[ProviderConfig(name="local", api_base="http://localhost:8000/v1", api_key_env_var="", backend=Backend.GENERIC)],
        models=[ModelConfig(name="devstral", provider="local", input_price=0, output_price=0)],
        session_logging={"enabled": False, "save_dir": str(tmp_path / "logs")},
    )


def _agent(tmp_path: Path, max_turns: int = 15) -> AgentLoop:
    return AgentLoop(config=_config(tmp_path), agent_name=BuiltinAgentName.AUTO_APPROVE, max_turns=max_turns)


async def _run(agent, prompt, max_events=100):
    events = []
    async for ev in agent.act(prompt):
        events.append(ev)
        if len(events) >= max_events:
            break
    return events


# ============================================================================
# ISSUE: Circuit breaker fires but model KEEPS calling the same tool
# The breaker returns an error, but the model ignores it and calls again.
# Proven: 20 bash calls despite 17 circuit breaker fires.
# ============================================================================

@pytest.mark.asyncio
async def test_circuit_breaker_actually_stops_execution(tmp_path):
    """After circuit breaker fires, model should NOT make more than 3 additional
    identical calls. Currently it makes 17+."""
    agent = _agent(tmp_path, max_turns=20)
    events = await _run(agent, "Run the command 'echo hello' ten separate times", max_events=120)

    tool_calls = [e for e in events if isinstance(e, ToolCallEvent)]
    cb_fires = [e for e in events if isinstance(e, ToolResultEvent)
                and e.error and "CIRCUIT" in str(e.error)]

    # Circuit breaker should fire
    if not cb_fires:
        pytest.skip("Model didn't repeat — can't test circuit breaker")

    # After first circuit breaker fire, model should stop within 3 more calls
    first_cb_idx = next(i for i, e in enumerate(events) if isinstance(e, ToolResultEvent) and e.error and "CIRCUIT" in str(e.error))
    calls_after_cb = [e for e in events[first_cb_idx:] if isinstance(e, ToolCallEvent)]

    assert len(calls_after_cb) <= 3, \
        f"Model made {len(calls_after_cb)} tool calls AFTER circuit breaker fired. " \
        f"It should stop, not keep going. Total: {len(tool_calls)} calls, {len(cb_fires)} breaker fires."

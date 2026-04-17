"""Real workflow tests — the EXACT scenarios users hit.

These run against the real vLLM backend with realistic prompts.
If these pass, DryDock is usable for normal work.
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
from drydock.core.config import Backend, ModelConfig, ProviderConfig, DrydockConfig
from drydock.core.types import AssistantEvent, ToolCallEvent, ToolResultEvent


def _vllm_ok():
    try:
        return httpx.get("http://localhost:8000/v1/models", timeout=3).status_code == 200
    except Exception:
        return False

pytestmark = pytest.mark.skipif(not _vllm_ok(), reason="vLLM not running")


def _agent(tmp_path, max_turns=20):
    config = DrydockConfig(
        active_model="devstral", auto_approve=True, enable_telemetry=False,
        include_project_context=False, system_prompt_id="cli",
        providers=[ProviderConfig(name="local", api_base="http://localhost:8000/v1", api_key_env_var="", backend=Backend.GENERIC)],
        models=[ModelConfig(name="devstral", provider="local", input_price=0, output_price=0)],
        session_logging={"enabled": False, "save_dir": str(tmp_path / "logs")},
    )
    os.chdir(tmp_path)
    return AgentLoop(config=config, agent_name=BuiltinAgentName.AUTO_APPROVE, max_turns=max_turns)


async def _run(agent, prompt, max_events=150):
    events = []
    async for ev in agent.act(prompt):
        events.append(ev)
        if len(events) >= max_events:
            break
    return events


@pytest.mark.asyncio
async def test_build_project_from_prd(tmp_path):
    """THE user's exact scenario: read PRD, build project.

    Must NOT trigger circuit breaker or crash.
    Must create at least some files.
    """
    (tmp_path / "PRD.md").write_text(
        "# CLI Tool\\n\\nBuild a Python CLI tool.\\n"
        "## Features\\n- Parse input files\\n- Show statistics\\n"
        "## Structure\\n- myapp/ package\\n- parser.py, cli.py\\n"
    )

    agent = _agent(tmp_path)
    events = await _run(agent, "review the PRD and get started")

    errors = [e for e in events if isinstance(e, ToolResultEvent) and e.error]
    cb_errors = [e for e in errors if "CIRCUIT BREAKER" in str(e.error)]
    force_stops = [e for e in events if isinstance(e, AssistantEvent) and e.stopped_by_middleware
                   and "FORCED STOP" in (e.content or "")]
    ordering = [e for e in errors if "Unexpected role" in str(e.error)]

    assert not force_stops, f"Session force-stopped during normal project setup"
    assert not ordering, f"Message ordering crash: {ordering[0].error[:100]}"
    assert len(cb_errors) <= 2, f"Circuit breaker fired {len(cb_errors)} times during normal work"

    # Should have created some files
    created = list(tmp_path.rglob("*.py"))
    assert len(created) > 0, "No Python files created"

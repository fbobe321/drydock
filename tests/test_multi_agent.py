"""Tests that DryDock actually uses multi-agent delegation.

These MUST run against the real vLLM backend.
They should FAIL if the model does everything in a single context
without using the task tool for subagent delegation.
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
from drydock.core.types import AssistantEvent, BaseEvent, ToolCallEvent, ToolResultEvent


def _vllm_ok():
    try:
        return httpx.get("http://localhost:8000/v1/models", timeout=3).status_code == 200
    except Exception:
        return False

pytestmark = pytest.mark.skipif(not _vllm_ok(), reason="vLLM not running")


def _config(tmp_path):
    return DrydockConfig(
        active_model="devstral", auto_approve=True, enable_telemetry=False,
        include_project_context=False, system_prompt_id="cli",
        providers=[ProviderConfig(name="local", api_base="http://localhost:8000/v1", api_key_env_var="", backend=Backend.GENERIC)],
        models=[ModelConfig(name="devstral", provider="local", input_price=0, output_price=0)],
        session_logging={"enabled": False, "save_dir": str(tmp_path / "logs")},
    )

def _agent(tmp_path, max_turns=12):
    return AgentLoop(config=_config(tmp_path), agent_name=BuiltinAgentName.AUTO_APPROVE, max_turns=max_turns)

async def _run(agent, prompt, max_events=80):
    events = []
    async for ev in agent.act(prompt):
        events.append(ev)
        if len(events) >= max_events:
            break
    return events


@pytest.mark.asyncio
async def test_agent_uses_subagent_naturally(tmp_path):
    """Agent should use subagents WITHOUT being told to.

    The system prompt must instruct the model to delegate complex tasks
    to subagents. If the model does everything alone, this test FAILS.

    The prompt does NOT mention subagents, task tool, or delegation.
    """
    # Create a multi-file project with enough complexity
    proj = tmp_path / "project"
    proj.mkdir()
    (proj / "main.py").write_text(
        "from auth import login\nfrom db import get_user\n\n"
        "def handle_request(username, password):\n"
        "    user = get_user(username)\n"
        "    if login(user, password):\n"
        "        return {'status': 'ok'}\n"
        "    return {'status': 'denied'}\n"
    )
    (proj / "auth.py").write_text(
        "import hashlib\n\ndef login(user, password):\n"
        "    hashed = hashlib.md5(password.encode()).hexdigest()\n"
        "    return user['password_hash'] == hashed\n"
    )
    (proj / "db.py").write_text(
        "USERS = {'admin': {'password_hash': '21232f297a57a5a743894a0e4a801fc3', 'role': 'admin'}}\n\n"
        "def get_user(username):\n    return USERS.get(username)\n"
    )
    (proj / "config.py").write_text("DB_HOST = 'localhost'\nDB_PORT = 5432\nSECRET = 'changeme'\n")
    (proj / "README.md").write_text("# My App\nA simple auth system.\n")

    agent = _agent(tmp_path)

    # Natural prompt — NO mention of subagents or task tool
    events = await _run(agent,
        f"Review the project at {proj}. It has multiple files. "
        f"Find any security issues and explain the architecture."
    )

    tool_calls = {}
    for ev in events:
        if isinstance(ev, ToolCallEvent):
            tool_calls[ev.tool_name] = tool_calls.get(ev.tool_name, 0) + 1

    used_task = "task" in tool_calls
    used_skill = "invoke_skill" in tool_calls

    assert used_task or used_skill, (
        f"FAIL: Agent did NOT use subagents for a multi-file review task. "
        f"Tool calls: {tool_calls}. "
        f"The system prompt must tell the model to use the task tool "
        f"for codebase exploration and complex multi-file analysis."
    )

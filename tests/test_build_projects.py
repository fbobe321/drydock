"""Build project tests — DryDock must actually BUILD things.

These are LONG tests (5-20 min each) that run against the real vLLM backend.
They ask DryDock to build real projects and verify:
1. Files are actually created
2. No circuit breaker kills the session
3. No message ordering crashes
4. No infinite loops (bash abuse, repetition)
5. Created code is valid Python (syntax check)

Run: pytest tests/test_build_projects.py -v -s
Expected runtime: 30-60 minutes total
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


def _agent(work_dir: Path, max_turns: int = 30):
    config = DrydockConfig(
        active_model="devstral", auto_approve=True, enable_telemetry=False,
        include_project_context=False, system_prompt_id="cli",
        providers=[ProviderConfig(name="local", api_base="http://localhost:8000/v1", api_key_env_var="", backend=Backend.GENERIC)],
        models=[ModelConfig(name="devstral", provider="local", input_price=0, output_price=0)],
        session_logging={"enabled": False, "save_dir": str(work_dir / ".logs")},
    )
    os.chdir(work_dir)
    return AgentLoop(config=config, agent_name=BuiltinAgentName.AUTO_APPROVE, max_turns=max_turns)


async def _run_and_evaluate(agent, prompt, work_dir, max_events=200):
    """Run agent and collect detailed metrics."""
    events = []
    tool_counts = {}
    errors = []
    circuit_breaker_fires = 0
    force_stops = 0
    ordering_crashes = 0

    async for ev in agent.act(prompt):
        events.append(ev)
        if isinstance(ev, ToolCallEvent):
            tool_counts[ev.tool_name] = tool_counts.get(ev.tool_name, 0) + 1
        elif isinstance(ev, ToolResultEvent) and ev.error:
            err = str(ev.error)
            errors.append(err[:100])
            if "CIRCUIT BREAKER" in err:
                circuit_breaker_fires += 1
            if "Unexpected role" in err:
                ordering_crashes += 1
        elif isinstance(ev, AssistantEvent) and ev.stopped_by_middleware:
            if "FORCED STOP" in (ev.content or ""):
                force_stops += 1
        if len(events) >= max_events:
            break

    # Count created files
    py_files = list(work_dir.rglob("*.py"))
    all_files = [f for f in work_dir.rglob("*") if f.is_file() and ".logs" not in str(f)]

    # Syntax check created Python files
    syntax_errors = 0
    for f in py_files:
        try:
            import ast
            ast.parse(f.read_text())
        except SyntaxError:
            syntax_errors += 1

    return {
        "events": len(events),
        "tool_counts": tool_counts,
        "errors": len(errors),
        "circuit_breaker_fires": circuit_breaker_fires,
        "force_stops": force_stops,
        "ordering_crashes": ordering_crashes,
        "py_files": len(py_files),
        "all_files": len(all_files),
        "syntax_errors": syntax_errors,
        "error_details": errors[:5],
    }


# ============================================================================
# Test 1: Build a CLI tool from a PRD
# ============================================================================

@pytest.mark.asyncio
async def test_build_cli_from_prd(tmp_path):
    """User scenario: 'review the PRD and get started'

    Must create a Python package with multiple files.
    Must NOT crash, loop, or get killed by circuit breaker.
    """
    (tmp_path / "PRD.md").write_text(
        "# File Counter CLI\n\n"
        "Build a Python CLI tool that counts files in a directory.\n\n"
        "## Features\n"
        "- Count files by extension\n"
        "- Show total size\n"
        "- Support --recursive flag\n\n"
        "## Structure\n"
        "- file_counter/ package\n"
        "- counter.py — core counting logic\n"
        "- cli.py — argparse interface\n"
        "- __init__.py\n"
    )

    agent = _agent(tmp_path, max_turns=25)
    result = await _run_and_evaluate(agent, "review the PRD and build the project", tmp_path)

    print(f"\n=== BUILD CLI RESULTS ===")
    print(f"Events: {result['events']}, Tools: {result['tool_counts']}")
    print(f"Files: {result['py_files']} Python, {result['all_files']} total")
    print(f"Errors: {result['errors']}, CB: {result['circuit_breaker_fires']}, Stops: {result['force_stops']}")
    if result['error_details']:
        print(f"Error samples: {result['error_details'][:3]}")

    assert result["ordering_crashes"] == 0, "Message ordering crash"
    assert result["force_stops"] == 0, "Session force-stopped"
    assert result["circuit_breaker_fires"] <= 2, f"Circuit breaker fired {result['circuit_breaker_fires']} times"
    assert result["py_files"] >= 2, f"Only {result['py_files']} Python files created (need 2+)"
    assert result["syntax_errors"] == 0, f"{result['syntax_errors']} files have syntax errors"


# ============================================================================
# Test 2: Build a simple Python script
# ============================================================================

@pytest.mark.asyncio
async def test_build_simple_script(tmp_path):
    """User scenario: 'write a Python script that...'

    Simpler task — should complete quickly with write_file.
    """
    agent = _agent(tmp_path, max_turns=15)
    result = await _run_and_evaluate(
        agent,
        "Write a Python script called calculator.py that implements add, subtract, multiply, divide functions and a main() that demos them",
        tmp_path,
    )

    print(f"\n=== SIMPLE SCRIPT RESULTS ===")
    print(f"Events: {result['events']}, Tools: {result['tool_counts']}")
    print(f"Files: {result['py_files']} Python")

    assert result["ordering_crashes"] == 0
    assert result["force_stops"] == 0
    assert result["py_files"] >= 1, "No Python file created"
    assert (tmp_path / "calculator.py").exists(), "calculator.py not created"


# ============================================================================
# Test 3: Add a feature to existing code
# ============================================================================

@pytest.mark.asyncio
async def test_add_feature_to_existing(tmp_path):
    """User scenario: 'add X to this code'

    Tests search_replace on existing files.
    """
    (tmp_path / "app.py").write_text(
        "def greet(name):\n"
        "    return f'Hello, {name}!'\n\n"
        "if __name__ == '__main__':\n"
        "    print(greet('World'))\n"
    )

    agent = _agent(tmp_path, max_turns=15)
    result = await _run_and_evaluate(
        agent,
        "Add a goodbye function to app.py and call it from main",
        tmp_path,
    )

    print(f"\n=== ADD FEATURE RESULTS ===")
    print(f"Events: {result['events']}, Tools: {result['tool_counts']}")

    assert result["ordering_crashes"] == 0
    assert result["force_stops"] == 0
    # Should have used search_replace or write_file
    edit_tools = result["tool_counts"].get("search_replace", 0) + result["tool_counts"].get("write_file", 0)
    assert edit_tools >= 1, f"No edits made (search_replace={result['tool_counts'].get('search_replace',0)}, write_file={result['tool_counts'].get('write_file',0)})"

    content = (tmp_path / "app.py").read_text()
    assert "goodbye" in content.lower() or "bye" in content.lower(), "Feature not added"


# ============================================================================
# Test 4: Fix a bug in existing code
# ============================================================================

@pytest.mark.asyncio
async def test_fix_bug(tmp_path):
    """User scenario: 'there's a bug, fix it'"""
    (tmp_path / "divide.py").write_text(
        "def safe_divide(a, b):\n"
        "    return a / b  # BUG: no zero check\n\n"
        "print(safe_divide(10, 0))\n"
    )

    agent = _agent(tmp_path, max_turns=15)
    result = await _run_and_evaluate(
        agent,
        "Fix the divide by zero bug in divide.py",
        tmp_path,
    )

    print(f"\n=== FIX BUG RESULTS ===")
    print(f"Events: {result['events']}, Tools: {result['tool_counts']}")

    assert result["ordering_crashes"] == 0
    assert result["force_stops"] == 0

    content = (tmp_path / "divide.py").read_text()
    has_fix = "if" in content or "try" in content or "== 0" in content or "ZeroDivision" in content
    assert has_fix, "Bug not fixed — no zero check added"


# ============================================================================
# Test 5: Multi-file project with tests
# ============================================================================

@pytest.mark.asyncio
async def test_build_project_with_tests(tmp_path):
    """User scenario: 'build a project with tests'

    The hardest test — must create package + test files.
    """
    agent = _agent(tmp_path, max_turns=30)
    result = await _run_and_evaluate(
        agent,
        "Create a Python package called 'mathlib' with functions for factorial, fibonacci, and is_prime. Include a test file.",
        tmp_path,
    )

    print(f"\n=== PROJECT WITH TESTS RESULTS ===")
    print(f"Events: {result['events']}, Tools: {result['tool_counts']}")
    print(f"Files: {result['py_files']} Python, {result['all_files']} total")
    print(f"Syntax errors: {result['syntax_errors']}")

    assert result["ordering_crashes"] == 0
    assert result["force_stops"] == 0
    assert result["py_files"] >= 2, f"Only {result['py_files']} Python files (need package + tests)"
    assert result["syntax_errors"] == 0, "Created files have syntax errors"

"""DryDock Workload Tests — Easy to Hard software tasks.

Tests the core use cases: BUILD, DEBUG, UPDATE across difficulty levels.
All tests run against the real vLLM backend.
Each test verifies DryDock can complete real work without crashing.

Difficulty levels:
- EASY: Single file, simple logic, <2 min
- MEDIUM: Multiple files, some complexity, 2-5 min
- HARD: Multi-module project, dependencies, 5-15 min

Run: pytest tests/test_workloads.py -v -s
Expected: 30-90 minutes total
"""

from __future__ import annotations

import ast
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
from drydock.core.types import AssistantEvent, ToolCallEvent, ToolResultEvent


def _vllm_ok():
    try:
        return httpx.get("http://localhost:8000/v1/models", timeout=3).status_code == 200
    except Exception:
        return False

pytestmark = pytest.mark.skipif(not _vllm_ok(), reason="vLLM not running")


# ============================================================================
# Test Infrastructure
# ============================================================================

def _agent(work_dir: Path, max_turns: int = 25):
    config = VibeConfig(
        active_model="devstral", auto_approve=True, enable_telemetry=False,
        include_project_context=False, system_prompt_id="cli",
        providers=[ProviderConfig(name="local", api_base="http://localhost:8000/v1", api_key_env_var="", backend=Backend.GENERIC)],
        models=[ModelConfig(name="devstral", provider="local", input_price=0, output_price=0)],
        session_logging={"enabled": False, "save_dir": str(work_dir / ".logs")},
    )
    os.chdir(work_dir)
    return AgentLoop(config=config, agent_name=BuiltinAgentName.AUTO_APPROVE, max_turns=max_turns)


class WorkloadResult:
    def __init__(self):
        self.events = 0
        self.tool_counts: dict[str, int] = {}
        self.errors: list[str] = []
        self.circuit_breaker = 0
        self.force_stops = 0
        self.ordering_crashes = 0

    @property
    def ok(self) -> bool:
        return self.force_stops == 0 and self.ordering_crashes == 0

    def summary(self) -> str:
        return (f"Events={self.events} Tools={self.tool_counts} "
                f"Errors={len(self.errors)} CB={self.circuit_breaker} "
                f"Stops={self.force_stops}")


async def run_workload(agent, prompt, work_dir, max_events=200) -> WorkloadResult:
    r = WorkloadResult()
    async for ev in agent.act(prompt):
        r.events += 1
        if isinstance(ev, ToolCallEvent):
            r.tool_counts[ev.tool_name] = r.tool_counts.get(ev.tool_name, 0) + 1
        elif isinstance(ev, ToolResultEvent) and ev.error:
            err = str(ev.error)[:100]
            r.errors.append(err)
            if "CIRCUIT BREAKER" in err:
                r.circuit_breaker += 1
            if "Unexpected role" in err:
                r.ordering_crashes += 1
        elif isinstance(ev, AssistantEvent) and ev.stopped_by_middleware:
            if "FORCED STOP" in (ev.content or "") or "STOPPED" in (ev.content or ""):
                r.force_stops += 1
        if r.events >= max_events:
            break
    return r


def check_files(work_dir: Path, expected_patterns: list[str]) -> list[str]:
    """Check that expected files were created. Returns list of missing."""
    missing = []
    for pattern in expected_patterns:
        matches = list(work_dir.rglob(pattern))
        if not matches:
            missing.append(pattern)
    return missing


def check_syntax(work_dir: Path) -> list[str]:
    """Check all .py files for syntax errors. Returns list of broken files."""
    broken = []
    for f in work_dir.rglob("*.py"):
        if ".logs" in str(f):
            continue
        try:
            ast.parse(f.read_text())
        except SyntaxError as e:
            broken.append(f"{f.name}: {e}")
    return broken


def check_content(file_path: Path, must_contain: list[str]) -> list[str]:
    """Check file contains expected strings. Returns missing ones."""
    if not file_path.exists():
        return [f"FILE MISSING: {file_path}"]
    content = file_path.read_text()
    return [s for s in must_contain if s not in content]


# ============================================================================
# BUILD: Easy
# ============================================================================

@pytest.mark.asyncio
async def test_build_easy_hello_world(tmp_path):
    """EASY BUILD: Create a hello world script."""
    agent = _agent(tmp_path, max_turns=10)
    r = await run_workload(agent, "Create hello.py that prints 'Hello, World!'", tmp_path)
    print(f"\n  {r.summary()}")

    assert r.ok, f"Session crashed: stops={r.force_stops}, ordering={r.ordering_crashes}"
    assert (tmp_path / "hello.py").exists(), "hello.py not created"
    assert "print" in (tmp_path / "hello.py").read_text()


@pytest.mark.asyncio
async def test_build_easy_fibonacci(tmp_path):
    """EASY BUILD: Create a fibonacci function."""
    agent = _agent(tmp_path, max_turns=10)
    r = await run_workload(agent, "Write fib.py with a fibonacci(n) function that returns the nth fibonacci number", tmp_path)
    print(f"\n  {r.summary()}")

    assert r.ok
    assert (tmp_path / "fib.py").exists()
    assert not check_syntax(tmp_path), f"Syntax errors: {check_syntax(tmp_path)}"


@pytest.mark.asyncio
async def test_build_easy_json_reader(tmp_path):
    """EASY BUILD: Create a script that reads JSON."""
    agent = _agent(tmp_path, max_turns=10)
    r = await run_workload(agent, "Write read_json.py that reads a JSON file passed as command line arg and pretty-prints it", tmp_path)
    print(f"\n  {r.summary()}")

    assert r.ok
    missing = check_content(tmp_path / "read_json.py", ["json", "import"])
    assert not missing, f"Missing in read_json.py: {missing}"


# ============================================================================
# BUILD: Medium
# ============================================================================

@pytest.mark.asyncio
async def test_build_medium_todo_app(tmp_path):
    """MEDIUM BUILD: Create a todo list with add/remove/list."""
    agent = _agent(tmp_path, max_turns=20)
    r = await run_workload(agent,
        "Create a todo list app: todo.py with functions add_todo(text), remove_todo(id), list_todos(). "
        "Store todos in a JSON file. Include a main() with argparse for CLI usage.",
        tmp_path)
    print(f"\n  {r.summary()}")

    assert r.ok
    py_files = list(tmp_path.rglob("*.py"))
    assert len(py_files) >= 1, "No Python files created"
    assert not check_syntax(tmp_path)


@pytest.mark.asyncio
async def test_build_medium_web_scraper(tmp_path):
    """MEDIUM BUILD: Create a URL content fetcher."""
    agent = _agent(tmp_path, max_turns=20)
    r = await run_workload(agent,
        "Create fetcher.py that takes a URL as argument, fetches it with requests, "
        "extracts the page title, and prints it. Handle errors gracefully.",
        tmp_path)
    print(f"\n  {r.summary()}")

    assert r.ok
    assert list(tmp_path.rglob("*.py")), "No Python files created"


@pytest.mark.asyncio
async def test_build_medium_csv_analyzer(tmp_path):
    """MEDIUM BUILD: Create a CSV statistics tool."""
    agent = _agent(tmp_path, max_turns=20)
    r = await run_workload(agent,
        "Create csv_stats.py that reads a CSV file and shows: "
        "row count, column names, min/max/avg for numeric columns. "
        "Use only stdlib (csv module, no pandas).",
        tmp_path)
    print(f"\n  {r.summary()}")

    assert r.ok
    assert list(tmp_path.rglob("*.py"))
    assert not check_syntax(tmp_path)


# ============================================================================
# BUILD: Hard
# ============================================================================

@pytest.mark.asyncio
async def test_build_hard_rest_api(tmp_path):
    """HARD BUILD: Create a multi-file REST API project."""
    (tmp_path / "PRD.md").write_text(
        "# Task API\n\nBuild a simple REST API for tasks.\n\n"
        "## Files\n"
        "- app.py — Flask/FastAPI app with routes\n"
        "- models.py — Task dataclass\n"
        "- storage.py — In-memory storage\n"
        "- requirements.txt\n\n"
        "## Endpoints\n"
        "- GET /tasks — list all\n"
        "- POST /tasks — create\n"
        "- DELETE /tasks/<id> — delete\n"
    )
    agent = _agent(tmp_path, max_turns=30)
    r = await run_workload(agent, "Review the PRD and build the project", tmp_path)
    print(f"\n  {r.summary()}")
    print(f"  Files: {[f.name for f in tmp_path.rglob('*.py')]}")

    assert r.ok
    assert len(list(tmp_path.rglob("*.py"))) >= 2, "Need at least 2 Python files"
    assert not check_syntax(tmp_path)


@pytest.mark.asyncio
async def test_build_hard_package_with_tests(tmp_path):
    """HARD BUILD: Create a Python package with tests."""
    agent = _agent(tmp_path, max_turns=30)
    r = await run_workload(agent,
        "Create a Python package called 'textutils' with: "
        "1. textutils/__init__.py "
        "2. textutils/transform.py — functions: reverse(s), capitalize_words(s), remove_vowels(s) "
        "3. tests/test_transform.py — pytest tests for each function "
        "4. setup.py or pyproject.toml",
        tmp_path)
    print(f"\n  {r.summary()}")
    print(f"  Files: {[str(f.relative_to(tmp_path)) for f in tmp_path.rglob('*.py')]}")

    assert r.ok
    assert len(list(tmp_path.rglob("*.py"))) >= 3, "Need package + tests"
    assert not check_syntax(tmp_path)


# ============================================================================
# DEBUG: Easy
# ============================================================================

@pytest.mark.asyncio
async def test_debug_easy_syntax_error(tmp_path):
    """EASY DEBUG: Fix a syntax error."""
    (tmp_path / "broken.py").write_text("def hello()\n    print('hi')\n")

    agent = _agent(tmp_path, max_turns=10)
    r = await run_workload(agent, "Fix the syntax error in broken.py", tmp_path)
    print(f"\n  {r.summary()}")

    assert r.ok
    assert not check_syntax(tmp_path), "Syntax error not fixed"


@pytest.mark.asyncio
async def test_debug_easy_logic_error(tmp_path):
    """EASY DEBUG: Fix a logic error."""
    (tmp_path / "maxval.py").write_text(
        "def find_max(numbers):\n"
        "    result = 0  # BUG: should be float('-inf')\n"
        "    for n in numbers:\n"
        "        if n > result:\n"
        "            result = n\n"
        "    return result\n\n"
        "print(find_max([-5, -3, -1]))  # Should print -1, prints 0\n"
    )
    agent = _agent(tmp_path, max_turns=10)
    r = await run_workload(agent, "find_max([-5,-3,-1]) returns 0 instead of -1. Fix it.", tmp_path)
    print(f"\n  {r.summary()}")

    assert r.ok
    content = (tmp_path / "maxval.py").read_text()
    assert "inf" in content or "None" in content or "numbers[0]" in content, "Bug not fixed"


@pytest.mark.asyncio
async def test_debug_easy_import_error(tmp_path):
    """EASY DEBUG: Fix a missing import."""
    (tmp_path / "dates.py").write_text(
        "def days_between(d1, d2):\n"
        "    date1 = datetime.strptime(d1, '%Y-%m-%d')\n"
        "    date2 = datetime.strptime(d2, '%Y-%m-%d')\n"
        "    return abs((date2 - date1).days)\n"
    )
    agent = _agent(tmp_path, max_turns=10)
    r = await run_workload(agent, "dates.py crashes with NameError: datetime. Fix it.", tmp_path)
    print(f"\n  {r.summary()}")

    assert r.ok
    missing = check_content(tmp_path / "dates.py", ["import", "datetime"])
    assert not missing


# ============================================================================
# DEBUG: Medium
# ============================================================================

@pytest.mark.asyncio
async def test_debug_medium_off_by_one(tmp_path):
    """MEDIUM DEBUG: Fix an off-by-one error in pagination."""
    (tmp_path / "paginate.py").write_text(
        "def paginate(items, page, per_page=10):\n"
        "    start = page * per_page  # BUG: should be (page-1)*per_page for 1-indexed\n"
        "    end = start + per_page\n"
        "    return items[start:end]\n\n"
        "# page 1 should return items 0-9, but returns 10-19\n"
        "data = list(range(100))\n"
        "print(paginate(data, 1))  # Prints [10-19] instead of [0-9]\n"
    )
    agent = _agent(tmp_path, max_turns=10)
    r = await run_workload(agent, "paginate(data, 1) returns items 10-19 instead of 0-9. Fix the pagination.", tmp_path)
    print(f"\n  {r.summary()}")

    assert r.ok
    content = (tmp_path / "paginate.py").read_text()
    assert "page - 1" in content or "page-1" in content or "(page" in content


# ============================================================================
# UPDATE: Easy
# ============================================================================

@pytest.mark.asyncio
async def test_update_easy_add_docstrings(tmp_path):
    """EASY UPDATE: Add docstrings to functions."""
    (tmp_path / "utils.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def multiply(a, b):\n    return a * b\n\n"
        "def divide(a, b):\n    if b == 0:\n        return None\n    return a / b\n"
    )
    agent = _agent(tmp_path, max_turns=10)
    r = await run_workload(agent, "Add docstrings to all functions in utils.py", tmp_path)
    print(f"\n  {r.summary()}")

    assert r.ok
    content = (tmp_path / "utils.py").read_text()
    assert '"""' in content or "'''" in content, "No docstrings added"


@pytest.mark.asyncio
async def test_update_easy_add_type_hints(tmp_path):
    """EASY UPDATE: Add type hints."""
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def concat(items):\n    return ', '.join(items)\n"
    )
    agent = _agent(tmp_path, max_turns=10)
    r = await run_workload(agent, "Add type hints to all functions in calc.py", tmp_path)
    print(f"\n  {r.summary()}")

    assert r.ok
    content = (tmp_path / "calc.py").read_text()
    assert "->" in content or ": " in content, "No type hints added"


# ============================================================================
# UPDATE: Medium
# ============================================================================

@pytest.mark.asyncio
async def test_update_medium_add_error_handling(tmp_path):
    """MEDIUM UPDATE: Add error handling to existing functions."""
    (tmp_path / "file_ops.py").write_text(
        "import json\n\n"
        "def read_config(path):\n"
        "    with open(path) as f:\n"
        "        return json.load(f)\n\n"
        "def write_config(path, data):\n"
        "    with open(path, 'w') as f:\n"
        "        json.dump(data, f)\n\n"
        "def delete_file(path):\n"
        "    import os\n"
        "    os.remove(path)\n"
    )
    agent = _agent(tmp_path, max_turns=15)
    r = await run_workload(agent,
        "Add try/except error handling to all functions in file_ops.py. "
        "Handle FileNotFoundError, json.JSONDecodeError, and PermissionError.",
        tmp_path)
    print(f"\n  {r.summary()}")

    assert r.ok
    content = (tmp_path / "file_ops.py").read_text()
    assert "try" in content and "except" in content, "No error handling added"


@pytest.mark.asyncio
async def test_update_medium_refactor_to_class(tmp_path):
    """MEDIUM UPDATE: Refactor functions into a class."""
    (tmp_path / "counter.py").write_text(
        "count = 0\n\n"
        "def increment():\n    global count\n    count += 1\n\n"
        "def decrement():\n    global count\n    count -= 1\n\n"
        "def get_count():\n    return count\n\n"
        "def reset():\n    global count\n    count = 0\n"
    )
    agent = _agent(tmp_path, max_turns=15)
    r = await run_workload(agent,
        "Refactor counter.py: replace the global variable with a Counter class "
        "that has increment(), decrement(), get_count(), and reset() methods.",
        tmp_path)
    print(f"\n  {r.summary()}")

    assert r.ok
    content = (tmp_path / "counter.py").read_text()
    assert "class" in content, "Not refactored to a class"
    assert not check_syntax(tmp_path)

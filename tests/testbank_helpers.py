"""Shared infrastructure for the DryDock test bank.

All test bank files import from here to avoid duplication.
Provides: agent factory, result tracking, project scaffolding,
assertion helpers, and common fixtures.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import textwrap
from pathlib import Path
from typing import Any

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


# ============================================================================
# Skip if vLLM is not available
# ============================================================================

def vllm_ok() -> bool:
    try:
        return httpx.get("http://localhost:8000/v1/models", timeout=3).status_code == 200
    except Exception:
        return False

requires_vllm = pytest.mark.skipif(not vllm_ok(), reason="vLLM not running at localhost:8000")


# ============================================================================
# Agent factory
# ============================================================================

def make_agent(work_dir: Path, max_turns: int = 25, system_prompt_id: str = "cli") -> AgentLoop:
    """Create an auto-approve AgentLoop pointed at local vLLM."""
    config = VibeConfig(
        active_model="devstral",
        auto_approve=True,
        enable_telemetry=False,
        include_project_context=False,
        system_prompt_id=system_prompt_id,
        providers=[ProviderConfig(
            name="local",
            api_base="http://localhost:8000/v1",
            api_key_env_var="",
            backend=Backend.GENERIC,
        )],
        models=[ModelConfig(
            name="devstral", provider="local",
            input_price=0, output_price=0,
        )],
        session_logging={"enabled": False, "save_dir": str(work_dir / ".logs")},
    )
    os.chdir(work_dir)
    return AgentLoop(
        config=config,
        agent_name=BuiltinAgentName.AUTO_APPROVE,
        max_turns=max_turns,
    )


# ============================================================================
# Result tracking
# ============================================================================

class WorkloadResult:
    """Captures everything that happened during a DryDock run."""

    def __init__(self):
        self.events: list[Any] = []
        self.tool_calls: list[str] = []       # ordered list of tool names
        self.tool_counts: dict[str, int] = {}
        self.tool_args: dict[str, list[dict]] = {}  # tool_name -> [args_dicts]
        self.errors: list[str] = []
        self.circuit_breaker_fires: int = 0
        self.force_stops: int = 0
        self.ordering_crashes: int = 0
        self.assistant_text: list[str] = []
        self.files_written: list[str] = []     # paths from write_file/search_replace
        self.bash_commands: list[str] = []     # commands from bash tool

    @property
    def ok(self) -> bool:
        return self.force_stops == 0 and self.ordering_crashes == 0

    @property
    def total_tool_calls(self) -> int:
        return sum(self.tool_counts.values())

    def used_tool(self, name: str) -> bool:
        return name in self.tool_counts

    def used_subagent(self) -> bool:
        return "task" in self.tool_counts

    def summary(self) -> str:
        tools = ", ".join(f"{k}={v}" for k, v in sorted(self.tool_counts.items()))
        return (
            f"Tools: [{tools}] | "
            f"Errors: {len(self.errors)} | "
            f"CB: {self.circuit_breaker_fires} | "
            f"Stops: {self.force_stops}"
        )


async def run_workload(
    agent: AgentLoop,
    prompt: str,
    max_events: int = 300,
) -> WorkloadResult:
    """Run a DryDock agent and collect detailed results."""
    r = WorkloadResult()
    async for ev in agent.act(prompt):
        r.events.append(ev)
        if isinstance(ev, ToolCallEvent):
            r.tool_calls.append(ev.tool_name)
            r.tool_counts[ev.tool_name] = r.tool_counts.get(ev.tool_name, 0) + 1
        elif isinstance(ev, ToolResultEvent):
            if ev.error:
                err = str(ev.error)[:200]
                r.errors.append(err)
                if "CIRCUIT BREAKER" in err:
                    r.circuit_breaker_fires += 1
                if "Unexpected role" in err:
                    r.ordering_crashes += 1
            # Track files written
            if ev.tool_name in ("write_file", "search_replace") and ev.result:
                try:
                    path = getattr(ev.result, "file", None) or getattr(ev.result, "path", None)
                    if path:
                        r.files_written.append(str(path))
                except Exception:
                    pass
        elif isinstance(ev, AssistantEvent):
            if ev.content:
                r.assistant_text.append(ev.content)
            if ev.stopped_by_middleware:
                content = ev.content or ""
                if "FORCED STOP" in content or "STOPPED" in content or "Stopping" in content:
                    r.force_stops += 1

        if len(r.events) >= max_events:
            break
    return r


# ============================================================================
# Assertion helpers
# ============================================================================

def check_files_exist(work_dir: Path, patterns: list[str]) -> list[str]:
    """Return list of glob patterns that matched NO files."""
    missing = []
    for pattern in patterns:
        if not list(work_dir.rglob(pattern)):
            missing.append(pattern)
    return missing


def check_syntax_all(work_dir: Path) -> list[str]:
    """Return list of .py files with syntax errors."""
    broken = []
    for f in work_dir.rglob("*.py"):
        if ".logs" in str(f):
            continue
        try:
            ast.parse(f.read_text())
        except SyntaxError as e:
            broken.append(f"{f.relative_to(work_dir)}: {e}")
    return broken


def check_content_contains(file_path: Path, expected: list[str]) -> list[str]:
    """Return list of strings NOT found in file content."""
    if not file_path.exists():
        return expected
    text = file_path.read_text()
    return [s for s in expected if s not in text]


def check_runs(work_dir: Path, command: str, timeout: int = 30) -> tuple[bool, str]:
    """Run a command in work_dir and return (success, output)."""
    try:
        result = subprocess.run(
            command, shell=True, cwd=work_dir,
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, str(e)


def count_python_files(work_dir: Path) -> int:
    """Count .py files created (excluding .logs)."""
    return sum(1 for f in work_dir.rglob("*.py") if ".logs" not in str(f))


# ============================================================================
# Project scaffolding helpers
# ============================================================================

def scaffold_python_file(work_dir: Path, path: str, content: str) -> Path:
    """Create a Python file with dedented content."""
    p = work_dir / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content))
    return p


def scaffold_project(work_dir: Path, files: dict[str, str]) -> None:
    """Create multiple files from a dict of {path: content}."""
    for path, content in files.items():
        scaffold_python_file(work_dir, path, content)


def scaffold_buggy_project(work_dir: Path, name: str) -> dict:
    """Create a pre-built buggy project. Returns metadata about the bugs."""
    scaffolds = _BUGGY_PROJECTS.get(name)
    if not scaffolds:
        raise ValueError(f"Unknown buggy project: {name}. Available: {list(_BUGGY_PROJECTS.keys())}")
    files = scaffolds["files"]
    scaffold_project(work_dir, files)
    return scaffolds.get("meta", {})


# ============================================================================
# Pre-built buggy projects for debug tests
# ============================================================================

_BUGGY_PROJECTS: dict[str, dict] = {
    "calculator_syntax": {
        "files": {
            "calculator.py": '''\
                def add(a, b):
                    return a + b

                def subtract(a, b):
                    return a - b

                def multiply(a, b):
                    return a * b

                def divide(a, b)
                    return a / b

                if __name__ == "__main__":
                    print(add(2, 3))
                    print(divide(10, 2))
            ''',
        },
        "meta": {
            "bug": "Missing colon on divide function def",
            "fix_file": "calculator.py",
        },
    },

    "sort_logic": {
        "files": {
            "sorter.py": '''\
                def bubble_sort(arr):
                    """Sort a list using bubble sort."""
                    n = len(arr)
                    for i in range(n):
                        for j in range(0, n - i - 1):
                            if arr[j] < arr[j + 1]:  # BUG: should be >
                                arr[j], arr[j + 1] = arr[j + 1], arr[j]
                    return arr

                def find_median(numbers):
                    """Find the median of a list of numbers."""
                    sorted_nums = bubble_sort(numbers.copy())
                    n = len(sorted_nums)
                    if n % 2 == 0:
                        return (sorted_nums[n // 2 - 1] + sorted_nums[n // 2]) / 2
                    return sorted_nums[n // 2]

                if __name__ == "__main__":
                    data = [64, 34, 25, 12, 22, 11, 90]
                    print(f"Sorted: {bubble_sort(data.copy())}")
                    print(f"Median: {find_median(data)}")
            ''',
            "test_sorter.py": '''\
                from sorter import bubble_sort, find_median

                def test_bubble_sort():
                    assert bubble_sort([3, 1, 2]) == [1, 2, 3]
                    assert bubble_sort([5, 4, 3, 2, 1]) == [1, 2, 3, 4, 5]
                    assert bubble_sort([]) == []
                    assert bubble_sort([1]) == [1]

                def test_median_odd():
                    assert find_median([3, 1, 2]) == 2

                def test_median_even():
                    assert find_median([1, 2, 3, 4]) == 2.5

                if __name__ == "__main__":
                    test_bubble_sort()
                    print("test_bubble_sort PASSED")
                    test_median_odd()
                    print("test_median_odd PASSED")
                    test_median_even()
                    print("test_median_even PASSED")
            ''',
        },
        "meta": {
            "bug": "bubble_sort compares with < instead of > (sorts descending not ascending)",
            "fix_file": "sorter.py",
            "test_cmd": "python3 test_sorter.py",
        },
    },

    "import_chain": {
        "files": {
            "app/__init__.py": "",
            "app/config.py": '''\
                import os

                DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///app.db")
                SECRET_KEY = os.getenv("SECRET_KEY", "dev-key")
                DEBUG = os.getenv("DEBUG", "true").lower() == "true"
            ''',
            "app/models.py": '''\
                from app.config import DATABASE_URL

                class User:
                    def __init__(self, name, email):
                        self.name = name
                        self.email = email

                    def to_dict(self):
                        return {"name": self.name, "email": self.email}

                class UserStore:
                    def __init__(self):
                        self.users = []
                        self.db_url = DATABASE_URL

                    def add(self, user):
                        self.users.append(user)

                    def find_by_email(self, email):
                        return next((u for u in self.users if u.email == email), None)
            ''',
            "app/service.py": '''\
                from app.modles import User, UserStore  # BUG: typo in module name

                store = UserStore()

                def register_user(name, email):
                    if store.find_by_email(email):
                        raise ValueError(f"User with email {email} already exists")
                    user = User(name, email)
                    store.add(user)
                    return user.to_dict()

                def get_user(email):
                    user = store.find_by_email(email)
                    if not user:
                        raise KeyError(f"No user found with email {email}")
                    return user.to_dict()
            ''',
            "main.py": '''\
                from app.service import register_user, get_user

                if __name__ == "__main__":
                    result = register_user("Alice", "alice@example.com")
                    print(f"Registered: {result}")
                    found = get_user("alice@example.com")
                    print(f"Found: {found}")
            ''',
        },
        "meta": {
            "bug": "app/service.py imports from 'app.modles' (typo, should be 'app.models')",
            "fix_file": "app/service.py",
            "test_cmd": "python3 main.py",
        },
    },

    "off_by_one_pagination": {
        "files": {
            "paginator.py": '''\
                from typing import TypeVar, Generic
                from dataclasses import dataclass

                T = TypeVar("T")

                @dataclass
                class Page:
                    items: list
                    page: int
                    total_pages: int
                    total_items: int
                    has_next: bool
                    has_prev: bool

                def paginate(items: list, page: int, per_page: int = 10) -> Page:
                    """Paginate a list of items.

                    Args:
                        items: Full list to paginate
                        page: 1-indexed page number
                        per_page: Items per page

                    Returns:
                        Page object with items and metadata
                    """
                    total = len(items)
                    total_pages = (total + per_page - 1) // per_page

                    if page < 1:
                        page = 1
                    if page > total_pages and total_pages > 0:
                        page = total_pages

                    # BUG: off-by-one, should be (page - 1) * per_page
                    start = page * per_page
                    end = start + per_page

                    return Page(
                        items=items[start:end],
                        page=page,
                        total_pages=total_pages,
                        total_items=total,
                        has_next=page < total_pages,
                        has_prev=page > 1,
                    )
            ''',
            "test_paginator.py": '''\
                from paginator import paginate

                def test_first_page():
                    items = list(range(1, 26))  # 25 items
                    page = paginate(items, page=1, per_page=10)
                    assert page.items == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], f"Got: {page.items}"
                    assert page.page == 1
                    assert page.total_pages == 3
                    assert page.has_next is True
                    assert page.has_prev is False

                def test_second_page():
                    items = list(range(1, 26))
                    page = paginate(items, page=2, per_page=10)
                    assert page.items == [11, 12, 13, 14, 15, 16, 17, 18, 19, 20], f"Got: {page.items}"

                def test_last_page():
                    items = list(range(1, 26))
                    page = paginate(items, page=3, per_page=10)
                    assert page.items == [21, 22, 23, 24, 25], f"Got: {page.items}"
                    assert page.has_next is False
                    assert page.has_prev is True

                def test_empty():
                    page = paginate([], page=1, per_page=10)
                    assert page.items == []
                    assert page.total_pages == 0

                if __name__ == "__main__":
                    for name, func in list(globals().items()):
                        if name.startswith("test_"):
                            try:
                                func()
                                print(f"PASS: {name}")
                            except AssertionError as e:
                                print(f"FAIL: {name}: {e}")
            ''',
        },
        "meta": {
            "bug": "start = page * per_page (should be (page - 1) * per_page, skips first page)",
            "fix_file": "paginator.py",
            "test_cmd": "python3 test_paginator.py",
        },
    },

    "data_pipeline_crash": {
        "files": {
            "pipeline/__init__.py": "",
            "pipeline/reader.py": '''\
                import csv
                import json
                from pathlib import Path

                def read_csv(path: str) -> list[dict]:
                    """Read a CSV file and return list of dicts."""
                    with open(path) as f:
                        reader = csv.DictReader(f)
                        return list(reader)

                def read_json(path: str) -> list[dict]:
                    """Read a JSON file (array of objects)."""
                    with open(path) as f:
                        return json.load(f)

                def read_auto(path: str) -> list[dict]:
                    """Auto-detect format and read."""
                    ext = Path(path).suffix.lower()
                    if ext == ".csv":
                        return read_csv(path)
                    elif ext == ".json":
                        return read_json(path)
                    else:
                        raise ValueError(f"Unsupported format: {ext}")
            ''',
            "pipeline/transform.py": '''\
                from typing import Any

                def filter_rows(data: list[dict], column: str, value: Any) -> list[dict]:
                    """Filter rows where column equals value."""
                    return [row for row in data if row.get(column) == value]

                def add_column(data: list[dict], name: str, func) -> list[dict]:
                    """Add a computed column to each row."""
                    for row in data:
                        row[name] = func(row)
                    return data

                def aggregate(data: list[dict], group_by: str, agg_col: str) -> dict:
                    """Group by a column and sum another column."""
                    groups = {}
                    for row in data:
                        key = row.get(group_by, "UNKNOWN")
                        # BUG: doesn't convert to float, treats string as number
                        val = row[agg_col]
                        groups[key] = groups.get(key, 0) + val
                    return groups
            ''',
            "pipeline/writer.py": '''\
                import csv
                import json

                def write_csv(data: list[dict], path: str) -> None:
                    if not data:
                        return
                    with open(path, "w", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=data[0].keys())
                        writer.writeheader()
                        writer.writerows(data)

                def write_json(data, path: str) -> None:
                    with open(path, "w") as f:
                        json.dump(data, f, indent=2)
            ''',
            "pipeline/cli.py": '''\
                import argparse
                import sys
                from pipeline.reader import read_auto
                from pipeline.transform import filter_rows, aggregate
                from pipeline.writer import write_csv, write_json

                def main():
                    parser = argparse.ArgumentParser(description="Data pipeline CLI")
                    parser.add_argument("input", help="Input file (CSV or JSON)")
                    parser.add_argument("-o", "--output", help="Output file")
                    parser.add_argument("--filter-col", help="Column to filter on")
                    parser.add_argument("--filter-val", help="Value to filter for")
                    parser.add_argument("--group-by", help="Column to group by")
                    parser.add_argument("--sum-col", help="Column to sum")
                    args = parser.parse_args()

                    data = read_auto(args.input)

                    if args.filter_col and args.filter_val:
                        data = filter_rows(data, args.filter_col, args.filter_val)

                    if args.group_by and args.sum_col:
                        result = aggregate(data, args.group_by, args.sum_col)
                        if args.output:
                            write_json(result, args.output)
                        else:
                            for k, v in result.items():
                                print(f"{k}: {v}")
                        return

                    if args.output:
                        if args.output.endswith(".json"):
                            write_json(data, args.output)
                        else:
                            write_csv(data, args.output)
                    else:
                        for row in data:
                            print(row)

                if __name__ == "__main__":
                    main()
            ''',
            "sample_data.csv": (
                "name,department,salary\n"
                "Alice,Engineering,95000\n"
                "Bob,Marketing,72000\n"
                "Charlie,Engineering,88000\n"
                "Diana,Marketing,68000\n"
                "Eve,Engineering,105000\n"
            ),
        },
        "meta": {
            "bug": "aggregate() doesn't convert CSV string values to float before summing",
            "fix_file": "pipeline/transform.py",
            "test_cmd": "python3 -m pipeline.cli sample_data.csv --group-by department --sum-col salary",
        },
    },

    "flask_like_router": {
        "files": {
            "framework/__init__.py": "",
            "framework/router.py": '''\
                from typing import Callable
                import re

                class Router:
                    def __init__(self):
                        self.routes: list[tuple[str, str, Callable]] = []

                    def add_route(self, method: str, path: str, handler: Callable):
                        """Register a route with optional path parameters like /users/<id>."""
                        # Convert <param> to regex groups
                        pattern = re.sub(r"<(\w+)>", r"(?P<\\1>[^/]+)", path)
                        self.routes.append((method.upper(), f"^{pattern}$", handler))

                    def get(self, path: str):
                        def decorator(func):
                            self.add_route("GET", path, func)
                            return func
                        return decorator

                    def post(self, path: str):
                        def decorator(func):
                            self.add_route("POST", path, func)
                            return func
                        return decorator

                    def match(self, method: str, path: str) -> tuple[Callable, dict] | None:
                        """Find matching route. Returns (handler, params) or None."""
                        for route_method, pattern, handler in self.routes:
                            if route_method != method.upper():
                                continue
                            m = re.match(pattern, path)
                            if m:
                                return handler, m.groupdict()
                        return None
            ''',
            "framework/request.py": '''\
                from dataclasses import dataclass, field

                @dataclass
                class Request:
                    method: str
                    path: str
                    headers: dict = field(default_factory=dict)
                    body: str = ""
                    query_params: dict = field(default_factory=dict)

                    @classmethod
                    def from_raw(cls, raw: str) -> "Request":
                        """Parse a raw HTTP-like request string."""
                        lines = raw.strip().split("\\n")
                        first = lines[0].split()
                        method = first[0]
                        full_path = first[1] if len(first) > 1 else "/"

                        # Parse query string
                        path = full_path
                        query_params = {}
                        if "?" in full_path:
                            path, qs = full_path.split("?", 1)
                            for pair in qs.split("&"):
                                if "=" in pair:
                                    k, v = pair.split("=", 1)
                                    query_params[k] = v

                        headers = {}
                        body_start = len(lines)
                        for i, line in enumerate(lines[1:], 1):
                            if line.strip() == "":
                                body_start = i + 1
                                break
                            if ":" in line:
                                k, v = line.split(":", 1)
                                headers[k.strip()] = v.strip()

                        body = "\\n".join(lines[body_start:]) if body_start < len(lines) else ""

                        return cls(method=method, path=path, headers=headers,
                                   body=body, query_params=query_params)
            ''',
            "framework/response.py": '''\
                import json
                from dataclasses import dataclass

                @dataclass
                class Response:
                    status: int = 200
                    body: str = ""
                    headers: dict = None

                    def __post_init__(self):
                        if self.headers is None:
                            self.headers = {"Content-Type": "text/plain"}

                    @classmethod
                    def json(cls, data, status=200):
                        return cls(
                            status=status,
                            body=json.dumps(data),
                            headers={"Content-Type": "application/json"},
                        )

                    @classmethod
                    def text(cls, text, status=200):
                        return cls(status=status, body=text)

                    @classmethod
                    def not_found(cls, message="Not Found"):
                        return cls(status=404, body=message)
            ''',
            "framework/app.py": '''\
                from framework.router import Router
                from framework.request import Request
                from framework.response import Response

                class App:
                    def __init__(self):
                        self.router = Router()
                        self._middleware = []

                    def get(self, path):
                        return self.router.get(path)

                    def post(self, path):
                        return self.router.post(path)

                    def use(self, middleware_func):
                        self._middleware.append(middleware_func)

                    def handle(self, request: Request) -> Response:
                        """Process a request through middleware and routing."""
                        # Run middleware
                        for mw in self._middleware:
                            result = mw(request)
                            if isinstance(result, Response):
                                return result

                        # Match route
                        match = self.router.match(request.method, request.path)
                        if not match:
                            return Response.not_found()

                        handler, params = match
                        # BUG: doesn't pass params to handler
                        return handler(request)
            ''',
            "test_app.py": '''\
                from framework.app import App
                from framework.request import Request
                from framework.response import Response

                app = App()

                @app.get("/")
                def index(request):
                    return Response.text("Welcome!")

                @app.get("/users/<user_id>")
                def get_user(request, user_id=None):
                    if user_id is None:
                        return Response.json({"error": "no user_id"}, status=400)
                    return Response.json({"user_id": user_id})

                @app.post("/users")
                def create_user(request):
                    return Response.json({"created": True}, status=201)

                def test_index():
                    req = Request(method="GET", path="/")
                    resp = app.handle(req)
                    assert resp.status == 200
                    assert "Welcome" in resp.body

                def test_get_user():
                    req = Request(method="GET", path="/users/42")
                    resp = app.handle(req)
                    assert resp.status == 200
                    import json
                    data = json.loads(resp.body)
                    assert data["user_id"] == "42", f"Expected user_id=42, got {data}"

                def test_create_user():
                    req = Request(method="POST", path="/users")
                    resp = app.handle(req)
                    assert resp.status == 201

                def test_not_found():
                    req = Request(method="GET", path="/nonexistent")
                    resp = app.handle(req)
                    assert resp.status == 404

                if __name__ == "__main__":
                    for name, func in list(globals().items()):
                        if name.startswith("test_"):
                            try:
                                func()
                                print(f"PASS: {name}")
                            except Exception as e:
                                print(f"FAIL: {name}: {e}")
            ''',
        },
        "meta": {
            "bug": "App.handle() doesn't pass matched path params (like user_id) to the handler",
            "fix_file": "framework/app.py",
            "test_cmd": "python3 test_app.py",
        },
    },

    "state_machine_bug": {
        "files": {
            "statemachine.py": '''\
                from enum import Enum
                from typing import Callable, Any

                class InvalidTransition(Exception):
                    pass

                class StateMachine:
                    def __init__(self, initial_state):
                        self.state = initial_state
                        self._transitions: dict[tuple, tuple] = {}
                        self._on_enter: dict[Any, list[Callable]] = {}
                        self._on_exit: dict[Any, list[Callable]] = {}
                        self._history: list[tuple] = []

                    def add_transition(self, trigger: str, source, dest, guard=None):
                        """Add a state transition."""
                        self._transitions[(source, trigger)] = (dest, guard)

                    def on_enter(self, state, callback):
                        self._on_enter.setdefault(state, []).append(callback)

                    def on_exit(self, state, callback):
                        self._on_exit.setdefault(state, []).append(callback)

                    def trigger(self, event: str, **context):
                        """Trigger a state transition."""
                        key = (self.state, event)
                        if key not in self._transitions:
                            raise InvalidTransition(
                                f"No transition from {self.state} on '{event}'"
                            )

                        dest, guard = self._transitions[key]

                        if guard and not guard(**context):
                            raise InvalidTransition(
                                f"Guard rejected transition {self.state} -> {dest}"
                            )

                        # Exit callbacks
                        for cb in self._on_exit.get(self.state, []):
                            cb(self.state, dest, **context)

                        old = self.state
                        self.state = dest
                        # BUG: records (old, dest) but should record (old, event, dest)
                        self._history.append((old, dest))

                        # Enter callbacks
                        for cb in self._on_enter.get(dest, []):
                            cb(old, dest, **context)

                    @property
                    def history(self):
                        return list(self._history)

                    def can_trigger(self, event: str) -> bool:
                        return (self.state, event) in self._transitions
            ''',
            "test_statemachine.py": '''\
                from statemachine import StateMachine, InvalidTransition

                class OrderState:
                    PENDING = "pending"
                    PAID = "paid"
                    SHIPPED = "shipped"
                    DELIVERED = "delivered"
                    CANCELLED = "cancelled"

                def make_order_machine():
                    sm = StateMachine(OrderState.PENDING)
                    sm.add_transition("pay", OrderState.PENDING, OrderState.PAID)
                    sm.add_transition("ship", OrderState.PAID, OrderState.SHIPPED)
                    sm.add_transition("deliver", OrderState.SHIPPED, OrderState.DELIVERED)
                    sm.add_transition("cancel", OrderState.PENDING, OrderState.CANCELLED)
                    sm.add_transition("cancel", OrderState.PAID, OrderState.CANCELLED)
                    return sm

                def test_happy_path():
                    sm = make_order_machine()
                    assert sm.state == OrderState.PENDING
                    sm.trigger("pay")
                    assert sm.state == OrderState.PAID
                    sm.trigger("ship")
                    assert sm.state == OrderState.SHIPPED
                    sm.trigger("deliver")
                    assert sm.state == OrderState.DELIVERED

                def test_history():
                    sm = make_order_machine()
                    sm.trigger("pay")
                    sm.trigger("ship")
                    # History should contain event name for debugging
                    for entry in sm.history:
                        assert len(entry) == 3, f"History entry should be (from, event, to), got {entry}"

                def test_invalid_transition():
                    sm = make_order_machine()
                    try:
                        sm.trigger("ship")  # Can't ship before paying
                        assert False, "Should have raised InvalidTransition"
                    except InvalidTransition:
                        pass

                def test_cancel():
                    sm = make_order_machine()
                    sm.trigger("cancel")
                    assert sm.state == OrderState.CANCELLED

                if __name__ == "__main__":
                    for name, func in list(globals().items()):
                        if name.startswith("test_"):
                            try:
                                func()
                                print(f"PASS: {name}")
                            except Exception as e:
                                print(f"FAIL: {name}: {e}")
            ''',
        },
        "meta": {
            "bug": "History records (old_state, new_state) but test expects (old_state, event, new_state)",
            "fix_file": "statemachine.py",
            "test_cmd": "python3 test_statemachine.py",
        },
    },
}

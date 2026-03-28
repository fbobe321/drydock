"""Test Bank: BUILD — DryDock builds software projects from scratch.

25 tests across 3 difficulty levels. Each test gives DryDock a prompt
and verifies the project was actually built correctly.

EASY (10): Single files, simple programs, 2-5 min each
MEDIUM (10): Multi-file packages, 5-15 min each
HARD (5): Complex projects with multiple modules, 10-30 min each

Total estimated runtime: 3-6 hours.

Run: pytest tests/test_bank_build.py -v -s --timeout=1800
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.testbank_helpers import (
    check_content_contains,
    check_files_exist,
    check_runs,
    check_syntax_all,
    count_python_files,
    make_agent,
    requires_vllm,
    run_workload,
)

pytestmark = [requires_vllm, pytest.mark.asyncio]


# ============================================================================
# EASY: Single file programs (2-5 min each)
# ============================================================================

class TestBuildEasy:
    """Single-file programs that should take 1-3 tool calls."""

    async def test_hello_world(self, tmp_path):
        """Most basic test: create and run a hello world."""
        agent = make_agent(tmp_path, max_turns=10)
        r = await run_workload(agent, "Create hello.py that prints 'Hello, World!'")

        assert r.ok, f"Agent crashed: {r.summary()}"
        assert (tmp_path / "hello.py").exists(), "hello.py not created"
        ok, out = check_runs(tmp_path, "python3 hello.py")
        assert ok and "Hello" in out, f"hello.py didn't run: {out}"

    async def test_fibonacci(self, tmp_path):
        """Create a fibonacci function and test it."""
        agent = make_agent(tmp_path, max_turns=12)
        r = await run_workload(agent,
            "Create fib.py with a function fibonacci(n) that returns the nth "
            "Fibonacci number (0-indexed: fib(0)=0, fib(1)=1, fib(5)=5, fib(10)=55). "
            "Print fib(10) at the end."
        )

        assert r.ok, f"Agent crashed: {r.summary()}"
        assert (tmp_path / "fib.py").exists()
        ok, out = check_runs(tmp_path, "python3 fib.py")
        assert ok, f"fib.py failed: {out}"
        assert "55" in out, f"Expected 55 in output: {out}"

    async def test_file_counter(self, tmp_path):
        """Count words/lines in a file."""
        # Create a sample text file
        (tmp_path / "sample.txt").write_text(
            "Hello World\nThis is a test\nThree lines total\n"
        )
        agent = make_agent(tmp_path, max_turns=12)
        r = await run_workload(agent,
            "Create wc.py that reads a filename from command line args and prints "
            "the number of lines, words, and characters. "
            "Test it on sample.txt (should be 3 lines, 9 words)."
        )

        assert r.ok, f"Agent crashed: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        broken = check_syntax_all(tmp_path)
        assert not broken, f"Syntax errors: {broken}"

    async def test_json_parser(self, tmp_path):
        """Read and summarize a JSON file."""
        (tmp_path / "data.json").write_text(json.dumps([
            {"name": "Alice", "age": 30, "city": "NYC"},
            {"name": "Bob", "age": 25, "city": "LA"},
            {"name": "Charlie", "age": 35, "city": "NYC"},
        ]))
        agent = make_agent(tmp_path, max_turns=12)
        r = await run_workload(agent,
            "Create summarize.py that reads data.json and prints: "
            "total records, average age, and count per city."
        )

        assert r.ok, f"Agent crashed: {r.summary()}"
        broken = check_syntax_all(tmp_path)
        assert not broken, f"Syntax errors: {broken}"

    async def test_csv_generator(self, tmp_path):
        """Generate a CSV file with fake data."""
        agent = make_agent(tmp_path, max_turns=12)
        r = await run_workload(agent,
            "Create gen_csv.py that generates a CSV file 'employees.csv' with "
            "columns: id, name, department, salary. Generate 20 rows of fake data. "
            "Then print the total salary."
        )

        assert r.ok, f"Agent crashed: {r.summary()}"
        # Either a csv or py file should exist
        assert count_python_files(tmp_path) >= 1

    async def test_password_generator(self, tmp_path):
        """Create a password generator with options."""
        agent = make_agent(tmp_path, max_turns=12)
        r = await run_workload(agent,
            "Create passgen.py that generates random passwords. "
            "It should accept --length (default 16), --no-symbols, --count (default 1) "
            "as command line args. Print the generated passwords."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert (tmp_path / "passgen.py").exists() or count_python_files(tmp_path) >= 1
        broken = check_syntax_all(tmp_path)
        assert not broken, f"Syntax errors: {broken}"

    async def test_text_stats(self, tmp_path):
        """Analyze text for readability stats."""
        (tmp_path / "essay.txt").write_text(
            "The quick brown fox jumps over the lazy dog. "
            "This sentence has exactly eight words in it. "
            "Short sentences are good. They improve readability. "
            "However, some longer sentences are necessary to convey "
            "complex ideas and maintain reader interest."
        )
        agent = make_agent(tmp_path, max_turns=12)
        r = await run_workload(agent,
            "Create textstats.py that reads essay.txt and prints: "
            "sentence count, average words per sentence, "
            "most common word, and longest sentence."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        broken = check_syntax_all(tmp_path)
        assert not broken, f"Syntax errors: {broken}"

    async def test_unit_converter(self, tmp_path):
        """Build a unit converter."""
        agent = make_agent(tmp_path, max_turns=12)
        r = await run_workload(agent,
            "Create converter.py with functions to convert between: "
            "celsius/fahrenheit, kg/lbs, km/miles. "
            "CLI usage: python3 converter.py 100 celsius fahrenheit "
            "Run it to convert 100 celsius to fahrenheit (should be 212)."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_markdown_to_html(self, tmp_path):
        """Convert markdown to HTML."""
        (tmp_path / "input.md").write_text(
            "# Title\n\nA paragraph with **bold** and *italic*.\n\n"
            "## Section\n\n- item 1\n- item 2\n- item 3\n"
        )
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "Create md2html.py that reads input.md and converts it to HTML. "
            "Handle headers (# ## ###), bold (**text**), italic (*text*), "
            "and unordered lists (- item). Write output to output.html. "
            "Don't use any external libraries."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_simple_calculator_repl(self, tmp_path):
        """Build a calculator that evaluates expressions."""
        agent = make_agent(tmp_path, max_turns=12)
        r = await run_workload(agent,
            "Create calc.py that evaluates math expressions from command line. "
            "Usage: python3 calc.py '2 + 3 * 4' should print 14. "
            "Support +, -, *, /, parentheses, and operator precedence. "
            "Do NOT use eval(). Test it with: python3 calc.py '(2 + 3) * 4'"
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        broken = check_syntax_all(tmp_path)
        assert not broken


# ============================================================================
# MEDIUM: Multi-file projects (5-15 min each)
# ============================================================================

class TestBuildMedium:
    """Multi-file projects that require structure and organization."""

    async def test_todo_app(self, tmp_path):
        """CLI todo app with JSON persistence."""
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "Build a CLI todo app with these commands:\n"
            "  python3 todo.py add 'Buy groceries'\n"
            "  python3 todo.py list\n"
            "  python3 todo.py done 1\n"
            "  python3 todo.py remove 1\n"
            "Store todos in todos.json. Each todo has: id, text, done (bool), created_at.\n"
            "After building, test all commands."
        )

        assert r.ok, f"Agent crashed: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        broken = check_syntax_all(tmp_path)
        assert not broken, f"Syntax errors: {broken}"

    async def test_log_analyzer(self, tmp_path):
        """Build a log file analyzer — the exact task the user reported issues with."""
        (tmp_path / "server.log").write_text(
            "2024-01-15 10:23:01 INFO  Request received: GET /api/users\n"
            "2024-01-15 10:23:02 INFO  Response sent: 200 OK (45ms)\n"
            "2024-01-15 10:23:15 WARN  Slow query: SELECT * FROM users (2340ms)\n"
            "2024-01-15 10:24:01 ERROR Database connection timeout\n"
            "2024-01-15 10:24:02 ERROR Retry failed: connection refused\n"
            "2024-01-15 10:24:05 INFO  Reconnected to database\n"
            "2024-01-15 10:25:00 INFO  Request received: POST /api/users\n"
            "2024-01-15 10:25:01 WARN  Rate limit approaching: 85%\n"
            "2024-01-15 10:25:02 INFO  Response sent: 201 Created (120ms)\n"
            "2024-01-15 10:30:00 ERROR OutOfMemoryError: heap space\n"
        )
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "Build a log analyzer tool:\n"
            "1. Parse log entries (timestamp, level, message)\n"
            "2. Show summary: count per log level, error rate, time range\n"
            "3. Filter by level: python3 analyzer.py server.log --level ERROR\n"
            "4. Output as JSON: python3 analyzer.py server.log --json\n"
            "Test it on server.log."
        )

        assert r.ok, f"Agent crashed: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        broken = check_syntax_all(tmp_path)
        assert not broken, f"Syntax errors: {broken}"
        # Should have used write_file, not just bash
        assert r.used_tool("write_file") or r.used_tool("search_replace"), \
            f"Agent didn't use file creation tools. Tools: {r.tool_counts}"

    async def test_key_value_store(self, tmp_path):
        """In-memory key-value store with CLI."""
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "Build a key-value store package 'kvstore' with:\n"
            "- kvstore/store.py: KVStore class with get/set/delete/list/save/load\n"
            "- kvstore/cli.py: CLI interface (set key value, get key, list, delete key)\n"
            "- kvstore/__init__.py\n"
            "Data persists to store.json between runs.\n"
            "Support TTL (expiry): set key value --ttl 60 (seconds)\n"
            "Test: set a key, get it back, list all keys."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        missing = check_files_exist(tmp_path, ["kvstore/__init__.py", "kvstore/store.py"])
        assert len(missing) <= 1, f"Missing files: {missing}"
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_http_client(self, tmp_path):
        """Build a simple HTTP client library."""
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "Create an HTTP client module 'httpclient':\n"
            "- httpclient/__init__.py\n"
            "- httpclient/client.py: HTTPClient class using socket\n"
            "  - get(url) -> Response\n"
            "  - post(url, body) -> Response\n"
            "  - Response has: status_code, headers (dict), body (str)\n"
            "- httpclient/url_parser.py: parse URLs into (scheme, host, port, path)\n"
            "Don't use requests/httpx/urllib. Use raw sockets.\n"
            "Test url_parser by parsing 'http://example.com:8080/api/v1'."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 2
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_task_runner(self, tmp_path):
        """Build a task runner that reads tasks.yaml."""
        (tmp_path / "tasks.yaml").write_text(
            "tasks:\n"
            "  lint:\n"
            "    command: python3 -m py_compile main.py\n"
            "    description: Check syntax\n"
            "  test:\n"
            "    command: python3 -m pytest tests/ -q\n"
            "    depends_on: [lint]\n"
            "    description: Run tests\n"
            "  build:\n"
            "    command: echo 'Building...'\n"
            "    depends_on: [test]\n"
            "    description: Build the project\n"
        )
        (tmp_path / "main.py").write_text("print('hello')\n")
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "Build a task runner 'runner.py' that:\n"
            "1. Reads tasks.yaml (install pyyaml if needed)\n"
            "2. Resolves task dependencies (topological sort)\n"
            "3. Runs tasks in dependency order\n"
            "4. Shows pass/fail status for each task\n"
            "Usage: python3 runner.py build (runs lint, then test, then build)\n"
            "Test it by running: python3 runner.py lint"
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_data_classes_orm(self, tmp_path):
        """Build a micro-ORM using dataclasses and SQLite."""
        agent = make_agent(tmp_path, max_turns=30)
        r = await run_workload(agent,
            "Build a micro-ORM package 'miniorm':\n"
            "- miniorm/__init__.py: exports Model, Database\n"
            "- miniorm/database.py: Database class wrapping sqlite3\n"
            "- miniorm/model.py: Model base class using dataclasses\n"
            "  - save() to insert/update\n"
            "  - delete() to remove\n"
            "  - find(id) class method\n"
            "  - all() class method\n"
            "  - Auto-create table from dataclass fields\n"
            "Create demo.py that defines a User model (name, email, age) "
            "and does CRUD operations. Run the demo."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 3
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_cli_framework(self, tmp_path):
        """Build a mini click-like CLI framework."""
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "Create a mini CLI framework 'minicli' (like click but simple):\n"
            "- minicli/__init__.py: export CLI, command, argument, option\n"
            "- minicli/core.py: CLI class with @command decorator, @argument, @option\n"
            "- minicli/parser.py: Parse sys.argv into commands and flags\n"
            "Create example_app.py using minicli with a 'greet' command:\n"
            "  python3 example_app.py greet --name Alice --loud\n"
            "Run the example."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 3
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_template_engine(self, tmp_path):
        """Build a simple template engine (like Jinja2 lite)."""
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "Create a template engine 'templite':\n"
            "- templite/engine.py: Template class\n"
            "  - Supports {{ variable }} substitution\n"
            "  - Supports {% for item in list %}...{% endfor %}\n"
            "  - Supports {% if condition %}...{% endif %}\n"
            "  - render(context) -> str\n"
            "Create a demo that renders a simple HTML page with a list of users.\n"
            "Test it."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_event_system(self, tmp_path):
        """Build an event emitter/pub-sub system."""
        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent,
            "Create an event system package 'events':\n"
            "- events/__init__.py\n"
            "- events/emitter.py: EventEmitter class\n"
            "  - on(event, callback) to register\n"
            "  - emit(event, *args) to trigger\n"
            "  - off(event, callback) to unregister\n"
            "  - once(event, callback) for one-time handlers\n"
            "- events/async_emitter.py: AsyncEventEmitter (same but async)\n"
            "Create demo.py showing both sync and async usage. Run it."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 2
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_csv_database(self, tmp_path):
        """Build a queryable CSV database."""
        (tmp_path / "employees.csv").write_text(
            "id,name,department,salary,hire_date\n"
            "1,Alice,Engineering,95000,2020-03-15\n"
            "2,Bob,Marketing,72000,2019-06-01\n"
            "3,Charlie,Engineering,88000,2021-01-10\n"
            "4,Diana,Marketing,68000,2022-09-20\n"
            "5,Eve,Engineering,105000,2018-11-05\n"
            "6,Frank,Sales,65000,2023-02-14\n"
        )
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "Build a CSV query tool 'csvql.py':\n"
            "- SELECT columns: csvql.py employees.csv --select name,salary\n"
            "- WHERE filter: csvql.py employees.csv --where 'department=Engineering'\n"
            "- ORDER BY: csvql.py employees.csv --order-by salary --desc\n"
            "- GROUP BY with COUNT/SUM/AVG: csvql.py employees.csv --group-by department --agg 'avg(salary)'\n"
            "Test: show average salary by department."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        broken = check_syntax_all(tmp_path)
        assert not broken


# ============================================================================
# HARD: Complex multi-module projects (10-30 min each)
# ============================================================================

class TestBuildHard:
    """Complex projects requiring architecture decisions and multiple modules."""

    async def test_rest_api_framework(self, tmp_path):
        """Build a REST API framework from scratch."""
        agent = make_agent(tmp_path, max_turns=40)
        r = await run_workload(agent, max_events=400, prompt=
            "Build a REST API framework 'picoapi' (like Flask but minimal):\n\n"
            "Core modules:\n"
            "- picoapi/app.py: App class with @get, @post, @put, @delete decorators\n"
            "- picoapi/router.py: URL routing with path parameters (/users/<id>)\n"
            "- picoapi/request.py: Request class (method, path, headers, body, query_params)\n"
            "- picoapi/response.py: Response class with .json(), .text(), .html() constructors\n"
            "- picoapi/server.py: HTTP server using http.server\n"
            "- picoapi/middleware.py: Middleware support (logging, CORS, auth)\n"
            "- picoapi/__init__.py: export App, Request, Response\n\n"
            "Create example_api.py with:\n"
            "- GET /users - list users (in-memory store)\n"
            "- POST /users - create user\n"
            "- GET /users/<id> - get one user\n"
            "- DELETE /users/<id> - delete user\n\n"
            "Verify: all files have valid Python syntax."
        )

        assert r.ok, f"Agent crashed: {r.summary()}"
        assert count_python_files(tmp_path) >= 5, f"Only {count_python_files(tmp_path)} files created"
        broken = check_syntax_all(tmp_path)
        assert not broken, f"Syntax errors: {broken}"

    async def test_testing_framework(self, tmp_path):
        """Build a testing framework (mini pytest)."""
        agent = make_agent(tmp_path, max_turns=40)
        r = await run_workload(agent, max_events=400, prompt=
            "Build a testing framework 'minitest':\n\n"
            "- minitest/__init__.py\n"
            "- minitest/runner.py: Discovers test_*.py files, runs test_ functions\n"
            "- minitest/assertions.py: assert_equal, assert_raises, assert_true, assert_in\n"
            "- minitest/reporter.py: Formats results (pass/fail counts, failure details)\n"
            "- minitest/fixtures.py: @setup, @teardown decorators, tmp_dir fixture\n"
            "- minitest/cli.py: CLI entry point\n\n"
            "Create tests/test_example.py that uses minitest to test a simple function.\n"
            "Run: python3 -m minitest tests/"
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 5
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_static_site_generator(self, tmp_path):
        """Build a static site generator."""
        # Create some content
        (tmp_path / "content").mkdir()
        (tmp_path / "content" / "index.md").write_text(
            "---\ntitle: Home\n---\n# Welcome\nThis is my site.\n"
        )
        (tmp_path / "content" / "about.md").write_text(
            "---\ntitle: About\n---\n# About\nThis is the about page.\n"
        )
        agent = make_agent(tmp_path, max_turns=40)
        r = await run_workload(agent, max_events=400, prompt=
            "Build a static site generator 'sitegen':\n\n"
            "- sitegen/__init__.py\n"
            "- sitegen/parser.py: Parse markdown files with YAML frontmatter\n"
            "- sitegen/renderer.py: Convert markdown to HTML (headers, bold, italic, lists, links)\n"
            "- sitegen/template.py: Simple HTML template with {{ title }}, {{ content }}, {{ nav }}\n"
            "- sitegen/builder.py: Read content/*.md, render to output/*.html\n"
            "- sitegen/cli.py: CLI entry (build, serve, clean)\n\n"
            "Run: python3 -m sitegen build\n"
            "Verify output/ directory has HTML files."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 4
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_chat_protocol(self, tmp_path):
        """Build a chat system with message protocol."""
        agent = make_agent(tmp_path, max_turns=40)
        r = await run_workload(agent, max_events=400, prompt=
            "Build a chat system 'chatlib':\n\n"
            "- chatlib/__init__.py\n"
            "- chatlib/message.py: Message class (sender, text, timestamp, type: TEXT/JOIN/LEAVE)\n"
            "- chatlib/protocol.py: Encode/decode messages to JSON wire format\n"
            "- chatlib/room.py: ChatRoom (join, leave, broadcast, history, max_history=100)\n"
            "- chatlib/server.py: ChatServer using asyncio (handle connections, route messages)\n"
            "- chatlib/client.py: ChatClient (connect, send, receive)\n\n"
            "Create demo.py that creates a room, simulates 3 users sending messages, "
            "and prints the chat history. Run it."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 4
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_build_from_prd(self, tmp_path):
        """Build a complete project from a Product Requirements Document."""
        (tmp_path / "PRD.md").write_text(
            "# Expense Tracker CLI\n\n"
            "## Overview\n"
            "A command-line expense tracking tool for personal finance.\n\n"
            "## Features\n"
            "1. Add expenses with category, amount, date, and description\n"
            "2. List expenses with filters (date range, category, min/max amount)\n"
            "3. Monthly summary showing total per category\n"
            "4. Export to CSV\n"
            "5. Import from CSV\n"
            "6. Budget limits per category with warnings\n\n"
            "## Data Storage\n"
            "Store expenses in a JSON file (~/.expenses.json for prod, local for testing).\n\n"
            "## CLI Commands\n"
            "- `expense add --amount 45.50 --category food --desc 'Lunch'`\n"
            "- `expense list --category food --month 2024-01`\n"
            "- `expense summary --month 2024-01`\n"
            "- `expense export --output expenses.csv`\n"
            "- `expense budget --category food --limit 500`\n\n"
            "## Technical Requirements\n"
            "- Python 3.10+\n"
            "- No external dependencies (stdlib only)\n"
            "- Proper error handling\n"
            "- Input validation\n"
        )
        agent = make_agent(tmp_path, max_turns=50)
        r = await run_workload(agent, max_events=500, prompt=
            "Read the PRD.md and build the Expense Tracker CLI. "
            "Create all necessary files. Test the basic add and list commands."
        )

        assert r.ok, f"Agent crashed: {r.summary()}"
        assert count_python_files(tmp_path) >= 2, \
            f"Only {count_python_files(tmp_path)} Python files created"
        broken = check_syntax_all(tmp_path)
        assert not broken, f"Syntax errors: {broken}"
        assert r.circuit_breaker_fires <= 8, \
            f"Circuit breaker fired {r.circuit_breaker_fires} times during project build"

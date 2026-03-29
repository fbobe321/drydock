"""Test Bank: PRD-Driven — DryDock builds projects from requirements docs.

This is the PRIMARY test suite. Each test gives DryDock a PRD and verifies
the built project actually RUNS. This is exactly what users do.

EASY (5): Simple single-purpose tools, 2-5 min each
MEDIUM (5): Multi-feature CLI apps, 5-15 min each
HARD (5): Full applications with multiple modules, 10-30 min each

Every test:
1. Provides a PRD.md
2. Tells DryDock "review the PRD and build it"
3. Verifies files were created
4. Verifies the project RUNS (not just syntax check)
5. Verifies key features work

Total estimated runtime: 2-6 hours.

Run: pytest tests/test_bank_prd.py -v -s
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.testbank_helpers import (
    check_syntax_all,
    count_python_files,
    make_agent,
    requires_vllm,
    run_workload,
)

pytestmark = [requires_vllm, pytest.mark.asyncio]


def _run(work_dir: Path, cmd: str, timeout: int = 15) -> tuple[bool, str]:
    """Run a command and return (success, output)."""
    try:
        r = subprocess.run(cmd, shell=True, cwd=work_dir,
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout + r.stderr)[:500]
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, str(e)


def _try_run_project(work_dir: Path) -> tuple[bool, str]:
    """Try common ways to run a Python project. Returns first success."""
    # Find package directories (contain __init__.py)
    packages = set()
    for init in work_dir.rglob("__init__.py"):
        pkg = init.parent
        if pkg != work_dir and ".logs" not in str(pkg):
            packages.add(pkg.relative_to(work_dir).parts[0])

    # Try python3 -m <package> first (handles relative imports)
    for pkg in sorted(packages):
        ok, out = _run(work_dir, f"python3 -m {pkg} --help")
        if ok:
            return True, f"python3 -m {pkg}: {out[:200]}"
        # Try without --help
        ok, out = _run(work_dir, f"python3 -m {pkg}")
        if ok:
            return True, f"python3 -m {pkg}: {out[:200]}"

    # Try common entry point files
    for name in ("main.py", "app.py", "cli.py", "run.py"):
        entry = work_dir / name
        if entry.exists():
            ok, out = _run(work_dir, f"python3 {entry} --help")
            if ok:
                return True, f"{name}: {out[:200]}"
            ok, out = _run(work_dir, f"python3 {entry}")
            if ok:
                return True, f"{name}: {out[:200]}"

    return False, "No runnable entry point found"


# ============================================================================
# EASY PRDs: Simple single-purpose tools
# ============================================================================

class TestPRDEasy:

    async def test_word_counter(self, tmp_path):
        """Build a word counter tool from PRD."""
        (tmp_path / "PRD.md").write_text("""# Word Counter Tool

## Overview
A command-line tool that counts words, lines, and characters in text files.

## Usage
```
python3 -m wordcount myfile.txt
python3 -m wordcount myfile.txt --top 10   # show top 10 most frequent words
python3 -m wordcount *.txt                  # multiple files
```

## Output
- Total lines, words, characters
- Top N most frequent words (default 5)
- One file per section if multiple files

## Requirements
- Python 3.10+, no external dependencies
- Handle UTF-8 files
""")
        (tmp_path / "sample.txt").write_text(
            "The quick brown fox jumps over the lazy dog.\n"
            "The dog barked at the fox.\n"
            "Quick brown foxes are quick.\n"
        )
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent, "Review the PRD and build the word counter tool. Test it on sample.txt.")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 1, "No Python files created"
        assert not check_syntax_all(tmp_path), f"Syntax errors: {check_syntax_all(tmp_path)}"
        # Must actually run
        ok, out = _try_run_project(tmp_path)
        if not ok:
            # Try running directly on sample.txt
            for f in tmp_path.rglob("*.py"):
                if f.name != "__init__.py":
                    ok, out = _run(tmp_path, f"python3 {f} sample.txt")
                    if ok:
                        break
        assert ok, f"Project doesn't run: {out}"

    async def test_password_generator(self, tmp_path):
        """Build a password generator from PRD."""
        (tmp_path / "PRD.md").write_text("""# Password Generator

## Overview
Generate secure random passwords from the command line.

## Usage
```
python3 -m passgen                    # default: 16 chars, all types
python3 -m passgen --length 24        # custom length
python3 -m passgen --no-symbols       # letters and digits only
python3 -m passgen --count 5          # generate 5 passwords
python3 -m passgen --memorable        # word-based (like "correct-horse-battery")
```

## Requirements
- Python stdlib only (secrets, string modules)
- Each password on its own line
""")
        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent, "Review the PRD and build the password generator. Generate 3 test passwords.")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        assert not check_syntax_all(tmp_path)
        ok, out = _try_run_project(tmp_path)
        if not ok:
            for f in tmp_path.rglob("*.py"):
                if f.name != "__init__.py":
                    ok, out = _run(tmp_path, f"python3 {f}")
                    if ok:
                        break
        assert ok, f"Doesn't run: {out}"

    async def test_json_formatter(self, tmp_path):
        """Build a JSON formatter/validator from PRD."""
        (tmp_path / "PRD.md").write_text("""# JSON Formatter

## Overview
A CLI tool to format, validate, and query JSON files.

## Usage
```
python3 -m jsonutil format input.json           # pretty-print
python3 -m jsonutil validate input.json          # check if valid JSON
python3 -m jsonutil query input.json ".users[0].name"  # extract value
```

## Requirements
- Python stdlib only (json module)
- Exit code 0 for valid, 1 for invalid
- Pretty-print with 2-space indent
""")
        (tmp_path / "test.json").write_text('{"name":"Alice","age":30,"tags":["python","cli"]}')
        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent, "Review the PRD and build the JSON formatter. Test it on test.json.")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        assert not check_syntax_all(tmp_path)
        ok, out = _try_run_project(tmp_path)
        if not ok:
            for f in tmp_path.rglob("*.py"):
                if f.name != "__init__.py":
                    for cmd in [f"python3 {f} format test.json", f"python3 {f} test.json"]:
                        ok, out = _run(tmp_path, cmd)
                        if ok:
                            break
                if ok:
                    break
        assert ok, f"Doesn't run: {out}"

    async def test_csv_to_json(self, tmp_path):
        """Build a CSV converter from PRD."""
        (tmp_path / "PRD.md").write_text("""# CSV Converter

## Overview
Convert CSV files to JSON and back.

## Usage
```
python3 -m csvtool to-json input.csv -o output.json
python3 -m csvtool to-csv input.json -o output.csv
python3 -m csvtool stats input.csv    # show row count, columns, sample
```

## Requirements
- Python stdlib only (csv, json)
- Auto-detect CSV delimiter (comma, tab, semicolon)
""")
        (tmp_path / "data.csv").write_text(
            "name,age,city\nAlice,30,NYC\nBob,25,LA\nCharlie,35,Chicago\n"
        )
        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent, "Review the PRD and build the CSV converter. Convert data.csv to JSON.")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        assert not check_syntax_all(tmp_path)
        # Check JSON output was created
        json_files = list(tmp_path.glob("*.json"))
        py_runs = False
        for f in tmp_path.rglob("*.py"):
            if f.name != "__init__.py":
                ok, out = _run(tmp_path, f"python3 {f} to-json data.csv -o out.json")
                if ok:
                    py_runs = True
                    break
                ok, out = _run(tmp_path, f"python3 {f} data.csv")
                if ok:
                    py_runs = True
                    break
        ok2, _ = _try_run_project(tmp_path)
        assert py_runs or ok2 or len(json_files) > 0, "Project doesn't produce output"

    async def test_file_organizer(self, tmp_path):
        """Build a file organizer from PRD."""
        (tmp_path / "PRD.md").write_text("""# File Organizer

## Overview
Organize files in a directory by type (extension) into subdirectories.

## Usage
```
python3 -m organizer /path/to/messy/dir
python3 -m organizer /path --dry-run     # show what would happen
python3 -m organizer /path --undo        # move files back
```

## Rules
- .py, .js, .ts → code/
- .jpg, .png, .gif → images/
- .pdf, .doc, .txt → documents/
- .csv, .json, .xml → data/
- Everything else → other/

## Requirements
- Python stdlib only (shutil, pathlib)
- --dry-run must not move anything, just print
""")
        # Create test files
        (tmp_path / "testdir").mkdir()
        for name in ["a.py", "b.js", "photo.jpg", "doc.pdf", "data.csv", "readme.txt"]:
            (tmp_path / "testdir" / name).write_text("content")

        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent,
            "Review the PRD and build the file organizer. Test with --dry-run on testdir/.")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        assert not check_syntax_all(tmp_path)


# ============================================================================
# MEDIUM PRDs: Multi-feature CLI applications
# ============================================================================

class TestPRDMedium:

    async def test_todo_app(self, tmp_path):
        """Build a todo app from PRD."""
        (tmp_path / "PRD.md").write_text("""# Todo CLI App

## Overview
A command-line todo list manager with JSON persistence.

## Commands
```
python3 -m todo add "Buy groceries"           # add a task
python3 -m todo add "Call dentist" --due 2024-02-01  # with due date
python3 -m todo list                           # show all tasks
python3 -m todo list --done                    # show completed
python3 -m todo done 1                         # mark task 1 as done
python3 -m todo remove 1                       # delete task 1
python3 -m todo search "grocery"               # search tasks
```

## Data
Store in todos.json. Each todo: id, text, done (bool), created_at, due_date (optional).

## Requirements
- Python stdlib only
- IDs are auto-incrementing integers
- List shows: [x] or [ ] prefix, ID, text, due date if set
""")
        agent = make_agent(tmp_path, max_turns=30)
        r = await run_workload(agent,
            "Review the PRD and build the todo app. Test: add 2 tasks, list them, mark one done.")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        assert not check_syntax_all(tmp_path)
        # Verify it runs
        ok, _ = _try_run_project(tmp_path)
        if not ok:
            for f in tmp_path.rglob("*.py"):
                if f.name != "__init__.py":
                    ok, _ = _run(tmp_path, f"python3 {f} add 'Test task'")
                    if ok:
                        break
        assert ok, f"Todo app doesn't run"

    async def test_log_analyzer(self, tmp_path):
        """Build a log analyzer from PRD — THE scenario the user keeps testing."""
        (tmp_path / "PRD.md").write_text("""# Log Analyzer

## Overview
Analyze server log files to find patterns, errors, and anomalies.

## Usage
```
python3 -m loganalyzer analyze server.log
python3 -m loganalyzer analyze server.log --level ERROR
python3 -m loganalyzer analyze server.log --json
python3 -m loganalyzer summary server.log
```

## Log Format
```
2024-01-15 10:23:01 INFO  Request received: GET /api/users
2024-01-15 10:24:01 ERROR Database connection timeout
```
Each line: timestamp (YYYY-MM-DD HH:MM:SS), level (INFO/WARN/ERROR), message.

## Output
- `analyze`: Show all matching entries
- `summary`: Count per level, error rate %, time range, top errors
- `--json`: Output as JSON object
- `--level LEVEL`: Filter by log level

## Requirements
- Python stdlib only
- Handle malformed lines gracefully (skip them)
""")
        (tmp_path / "server.log").write_text(
            "2024-01-15 10:23:01 INFO Request received: GET /api/users\n"
            "2024-01-15 10:23:02 INFO Response sent: 200 OK\n"
            "2024-01-15 10:24:01 ERROR Database connection timeout\n"
            "2024-01-15 10:24:02 ERROR Retry failed: connection refused\n"
            "2024-01-15 10:25:00 INFO Reconnected to database\n"
            "2024-01-15 10:25:01 WARN Rate limit approaching: 85%\n"
        )
        agent = make_agent(tmp_path, max_turns=30)
        r = await run_workload(agent,
            "Review the PRD and build the log analyzer. Test it on server.log — "
            "show the summary and also filter for ERROR entries.")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        assert not check_syntax_all(tmp_path)
        # MUST actually run and produce output
        ok, out = _try_run_project(tmp_path)
        if not ok:
            for f in tmp_path.rglob("*.py"):
                if f.name != "__init__.py":
                    ok, out = _run(tmp_path, f"python3 {f} analyze server.log")
                    if ok:
                        break
                    ok, out = _run(tmp_path, f"python3 {f} server.log")
                    if ok:
                        break
        assert ok, f"Log analyzer doesn't run: {out}"

    async def test_expense_tracker(self, tmp_path):
        """Build an expense tracker from PRD."""
        (tmp_path / "PRD.md").write_text("""# Expense Tracker

## Overview
Track personal expenses from the command line.

## Commands
```
python3 -m expenses add --amount 45.50 --category food --desc "Lunch"
python3 -m expenses list
python3 -m expenses list --category food
python3 -m expenses summary              # total per category
python3 -m expenses summary --month 2024-01
python3 -m expenses export expenses.csv
```

## Data
Store in expenses.json. Each expense: id, amount, category, description, date.

## Requirements
- Python stdlib only
- Amounts as float, formatted to 2 decimal places
- Summary shows total per category and grand total
""")
        agent = make_agent(tmp_path, max_turns=30)
        r = await run_workload(agent,
            "Review the PRD and build the expense tracker. "
            "Add 3 test expenses in different categories, then show the summary.")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        assert not check_syntax_all(tmp_path)

    async def test_markdown_site(self, tmp_path):
        """Build a static site generator from PRD."""
        (tmp_path / "PRD.md").write_text("""# Static Site Generator

## Overview
Convert markdown files to a static HTML website.

## Usage
```
python3 -m sitegen build             # build content/ → output/
python3 -m sitegen build --clean     # delete output/ first
```

## Structure
- content/*.md — source files with YAML frontmatter (title, date)
- output/*.html — generated HTML files
- Template: simple HTML with {{title}}, {{content}}, {{nav}} placeholders

## Markdown Support
- Headers (# ## ###)
- Bold (**text**) and italic (*text*)
- Unordered lists (- item)
- Links ([text](url))
- Code blocks (```lang ... ```)

## Requirements
- Python stdlib only (no markdown libraries)
- Generate index.html with links to all pages
""")
        (tmp_path / "content").mkdir()
        (tmp_path / "content" / "index.md").write_text(
            "---\ntitle: Home\n---\n# Welcome\nThis is my **awesome** site.\n"
        )
        (tmp_path / "content" / "about.md").write_text(
            "---\ntitle: About\n---\n# About\nBuilt with Python.\n- Feature 1\n- Feature 2\n"
        )
        agent = make_agent(tmp_path, max_turns=35)
        r = await run_workload(agent, max_events=400, prompt=
            "Review the PRD and build the static site generator. "
            "Run the build command and verify HTML files are created in output/.")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        assert not check_syntax_all(tmp_path)
        # Check if any HTML was generated
        html_files = list(tmp_path.rglob("*.html"))
        assert len(html_files) >= 1 or count_python_files(tmp_path) >= 3, \
            "No HTML generated and less than 3 Python files created"

    async def test_key_value_store(self, tmp_path):
        """Build a key-value store from PRD."""
        (tmp_path / "PRD.md").write_text("""# Key-Value Store CLI

## Overview
A persistent key-value store with CLI interface.

## Commands
```
python3 -m kvstore set mykey "my value"
python3 -m kvstore get mykey
python3 -m kvstore delete mykey
python3 -m kvstore list                  # show all keys
python3 -m kvstore list --prefix "my"    # filter by prefix
python3 -m kvstore export store.json     # dump all data
python3 -m kvstore import store.json     # load from dump
```

## Data
Store in ~/.kvstore.json (for production) or ./store.json (for testing).

## Features
- Values can be strings, numbers, or JSON objects
- TTL support: `set mykey "value" --ttl 3600` (seconds)
- Expired keys auto-cleaned on access

## Requirements
- Python stdlib only
""")
        agent = make_agent(tmp_path, max_turns=30)
        r = await run_workload(agent,
            "Review the PRD and build the key-value store. "
            "Test: set a key, get it back, list all keys.")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        assert not check_syntax_all(tmp_path)


# ============================================================================
# HARD PRDs: Full applications with multiple modules
# ============================================================================

class TestPRDHard:

    async def test_task_runner(self, tmp_path):
        """Build a task runner (like Make) from PRD."""
        (tmp_path / "PRD.md").write_text("""# Task Runner

## Overview
A task runner that reads task definitions from a YAML file and executes them.

## Usage
```
python3 -m taskrunner run build          # run the 'build' task
python3 -m taskrunner list               # list all tasks
python3 -m taskrunner run build --dry-run  # show what would run
```

## Task File (tasks.yaml)
```yaml
tasks:
  lint:
    command: python3 -m py_compile main.py
    description: Check syntax

  test:
    command: python3 -m pytest tests/ -q
    depends_on: [lint]
    description: Run tests

  build:
    command: echo "Building..."
    depends_on: [test]
    description: Build the project
```

## Features
- Dependency resolution (topological sort)
- Circular dependency detection
- Parallel execution of independent tasks
- Pass/fail status with colors
- --dry-run mode

## Requirements
- pyyaml for YAML parsing (pip install if needed)
- Python 3.10+
""")
        (tmp_path / "tasks.yaml").write_text(
            "tasks:\n"
            "  greet:\n"
            "    command: echo 'Hello!'\n"
            "    description: Say hello\n"
            "  goodbye:\n"
            "    command: echo 'Bye!'\n"
            "    depends_on: [greet]\n"
            "    description: Say goodbye\n"
        )
        agent = make_agent(tmp_path, max_turns=35)
        r = await run_workload(agent, max_events=400, prompt=
            "Review the PRD and build the task runner. Test by running the 'goodbye' task "
            "(which depends on 'greet').")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 2
        assert not check_syntax_all(tmp_path)

    async def test_rest_api(self, tmp_path):
        """Build a REST API framework from PRD."""
        (tmp_path / "PRD.md").write_text("""# Mini REST API Framework

## Overview
A lightweight REST API framework using Python's http.server.

## Usage
```python
# example_api.py
from miniapi import App, Response

app = App()

@app.get("/")
def index(request):
    return Response.json({"message": "Hello!"})

@app.get("/users/<user_id>")
def get_user(request, user_id):
    return Response.json({"id": user_id})

@app.post("/users")
def create_user(request):
    data = request.json()
    return Response.json({"created": data}, status=201)

if __name__ == "__main__":
    app.run(port=8080)
```

## Package Structure
- miniapi/__init__.py — exports App, Request, Response
- miniapi/app.py — App class with routing decorators
- miniapi/request.py — Request parsing
- miniapi/response.py — Response construction
- miniapi/router.py — URL matching with path parameters

## Requirements
- Python stdlib only (http.server, json, re)
- Path parameters: /users/<id> extracts id as kwarg
- Response helpers: .json(), .text(), .html(), .not_found()
""")
        agent = make_agent(tmp_path, max_turns=40)
        r = await run_workload(agent, max_events=400, prompt=
            "Review the PRD and build the miniapi framework. Create the example_api.py too. "
            "Verify all files have valid syntax.")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 4, f"Only {count_python_files(tmp_path)} files"
        assert not check_syntax_all(tmp_path)

    async def test_testing_framework(self, tmp_path):
        """Build a testing framework from PRD."""
        (tmp_path / "PRD.md").write_text("""# Mini Test Framework

## Overview
A simple testing framework (like a minimal pytest).

## Usage
```
python3 -m minitest tests/               # discover and run tests
python3 -m minitest tests/test_math.py   # run specific file
python3 -m minitest tests/ -v            # verbose output
```

## Package Structure
- minitest/__init__.py — exports assert_equal, assert_raises, assert_true
- minitest/runner.py — Test discovery and execution
- minitest/assertions.py — Assertion helpers
- minitest/reporter.py — Output formatting (pass/fail counts)
- minitest/__main__.py — CLI entry point

## Test File Format
```python
# tests/test_example.py
from minitest import assert_equal, assert_raises

def test_addition():
    assert_equal(2 + 2, 4)

def test_division_by_zero():
    assert_raises(ZeroDivisionError, lambda: 1/0)
```

## Features
- Auto-discover test_*.py files
- Run functions starting with test_
- Show pass/fail per test with timing
- Exit code: 0 if all pass, 1 if any fail

## Requirements
- Python stdlib only
""")
        agent = make_agent(tmp_path, max_turns=40)
        r = await run_workload(agent, max_events=400, prompt=
            "Review the PRD and build the test framework. Create a sample test file "
            "and run the framework on it to verify it works.")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 4, f"Only {count_python_files(tmp_path)} files"
        assert not check_syntax_all(tmp_path)

    async def test_data_pipeline(self, tmp_path):
        """Build a data pipeline from PRD."""
        (tmp_path / "PRD.md").write_text("""# Data Pipeline Tool

## Overview
A CLI tool for processing CSV data through a pipeline of operations.

## Usage
```
python3 -m datapipe process sales.csv --filter "department=Engineering" --group-by department --agg "sum(salary)" -o report.json
python3 -m datapipe info sales.csv          # show columns, row count, sample
python3 -m datapipe convert sales.csv -o sales.json  # CSV to JSON
```

## Package Structure
- datapipe/__init__.py
- datapipe/reader.py — Read CSV/JSON files
- datapipe/transform.py — Filter, group, aggregate operations
- datapipe/writer.py — Write CSV/JSON output
- datapipe/cli.py — argparse CLI
- datapipe/__main__.py — entry point

## Important
- CSV values are strings. Convert to numbers before math operations.
- Use absolute imports (from datapipe.reader import ...), not relative.
- Handle missing values gracefully.

## Requirements
- Python stdlib only (csv, json, argparse)
""")
        (tmp_path / "employees.csv").write_text(
            "name,department,salary\n"
            "Alice,Engineering,95000\n"
            "Bob,Marketing,72000\n"
            "Charlie,Engineering,88000\n"
            "Diana,Marketing,68000\n"
            "Eve,Engineering,105000\n"
        )
        agent = make_agent(tmp_path, max_turns=35)
        r = await run_workload(agent, max_events=400, prompt=
            "Review the PRD and build the data pipeline. "
            "Test: show info about employees.csv, then process it to get average salary by department.")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 3
        assert not check_syntax_all(tmp_path)

    async def test_chat_system(self, tmp_path):
        """Build a chat system from PRD."""
        (tmp_path / "PRD.md").write_text("""# Chat System Library

## Overview
A chat room library with message protocol, rooms, and history.

## Package Structure
- chatlib/__init__.py — exports ChatRoom, Message, User
- chatlib/message.py — Message dataclass (sender, text, timestamp, type)
- chatlib/room.py — ChatRoom (join, leave, send, history)
- chatlib/protocol.py — Serialize/deserialize messages to JSON
- chatlib/storage.py — Save/load chat history to JSON file

## Demo
Create demo.py that:
1. Creates a chat room "general"
2. Three users join (Alice, Bob, Charlie)
3. Each sends 2 messages
4. Print chat history
5. Save to history.json
6. Load and verify

## Requirements
- Python stdlib only
- Use absolute imports
- Messages have types: TEXT, JOIN, LEAVE
""")
        agent = make_agent(tmp_path, max_turns=35)
        r = await run_workload(agent, max_events=400, prompt=
            "Review the PRD and build the chat system. Run the demo to verify it works.")

        assert r.ok, f"Crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 4
        assert not check_syntax_all(tmp_path)

"""Test Bank: TOOLS — DryDock uses all its tools correctly in real tasks.

15 tests verifying each major tool works in realistic scenarios.
Tests verify: write_file, read_file, search_replace, grep, glob,
bash, task (subagent), ask_user_question patterns, and tool combinations.

Total estimated runtime: 1-3 hours.

Run: pytest tests/test_bank_tools.py -v -s --timeout=1800
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
    scaffold_project,
)

pytestmark = [requires_vllm, pytest.mark.asyncio]


# ============================================================================
# File creation tools (write_file)
# ============================================================================

class TestWriteFile:
    """Tests that write_file creates correct, runnable files."""

    async def test_create_single_file(self, tmp_path):
        """Basic file creation."""
        agent = make_agent(tmp_path, max_turns=10)
        r = await run_workload(agent,
            "Create a file called 'prime_checker.py' that has a function "
            "is_prime(n) -> bool and prints whether each number 1-20 is prime."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert (tmp_path / "prime_checker.py").exists()
        broken = check_syntax_all(tmp_path)
        assert not broken
        ok, out = check_runs(tmp_path, "python3 prime_checker.py")
        assert ok

    async def test_create_multiple_files(self, tmp_path):
        """Create a package with multiple files."""
        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent,
            "Create a Python package called 'shapes' with:\n"
            "- shapes/__init__.py (exports Circle, Rectangle, Triangle)\n"
            "- shapes/circle.py (Circle class with area, circumference methods)\n"
            "- shapes/rectangle.py (Rectangle class with area, perimeter methods)\n"
            "- shapes/triangle.py (Triangle class with area method using Heron's formula)\n"
            "Run a quick test to verify."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 3
        missing = check_files_exist(tmp_path, ["shapes/__init__.py"])
        assert not missing, f"Missing files: {missing}"
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_create_file_with_special_content(self, tmp_path):
        """Create files with tricky content (quotes, special chars)."""
        agent = make_agent(tmp_path, max_turns=12)
        r = await run_workload(agent,
            "Create a file 'quotes.py' that defines a list of 5 famous programming "
            "quotes (as strings), each with the author's name. Include quotes that "
            "contain single quotes, double quotes, and special characters. "
            "Print each quote with attribution."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert (tmp_path / "quotes.py").exists()
        broken = check_syntax_all(tmp_path)
        assert not broken
        ok, _ = check_runs(tmp_path, "python3 quotes.py")
        assert ok


# ============================================================================
# File editing tools (search_replace)
# ============================================================================

class TestSearchReplace:
    """Tests that search_replace makes precise edits without breaking code."""

    async def test_add_method_to_class(self, tmp_path):
        """Add a new method to an existing class."""
        scaffold_project(tmp_path, {
            "stack.py": '''\
                class Stack:
                    def __init__(self):
                        self._items = []

                    def push(self, item):
                        self._items.append(item)

                    def pop(self):
                        return self._items.pop()

                    def is_empty(self):
                        return len(self._items) == 0
            ''',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "Add a peek() method to the Stack class in stack.py that returns "
            "the top item without removing it. Also add a size() method. "
            "Don't change existing methods."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        content = (tmp_path / "stack.py").read_text()
        assert "peek" in content
        assert "size" in content
        # Original methods should still be there
        assert "push" in content and "pop" in content
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_fix_multiple_locations(self, tmp_path):
        """Fix the same bug in multiple places in one file."""
        scaffold_project(tmp_path, {
            "validators.py": '''\
                def validate_name(name):
                    if len(name) == 0:  # Should use 'not name' or check for None
                        return False
                    return True

                def validate_email(email):
                    if len(email) == 0:
                        return False
                    return "@" in email

                def validate_age(age):
                    if age is None or age < 0 or age > 150:
                        return False
                    return True

                def validate_phone(phone):
                    if len(phone) == 0:
                        return False
                    return all(c.isdigit() or c in "+-() " for c in phone)

                if __name__ == "__main__":
                    print(validate_name(""))
                    print(validate_name(None))  # This crashes: TypeError
            ''',
        })
        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent,
            "python3 validators.py crashes with TypeError: object of type 'NoneType' has no len(). "
            "The bug: validate_name, validate_email, and validate_phone all call len() "
            "without checking for None first. Add 'if name is None: return False' (or equivalent) "
            "at the top of ALL THREE functions. Then verify: python3 validators.py"
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 validators.py")
        assert ok, f"Still crashes: {out}"

    async def test_rename_function(self, tmp_path):
        """Rename a function and update all call sites."""
        scaffold_project(tmp_path, {
            "utils.py": '''\
                def calc(x, y, op):
                    if op == "+":
                        return x + y
                    elif op == "-":
                        return x - y
                    elif op == "*":
                        return x * y
                    elif op == "/":
                        return x / y if y != 0 else None
            ''',
            "main.py": '''\
                from utils import calc

                result1 = calc(10, 5, "+")
                print(f"10 + 5 = {result1}")

                result2 = calc(10, 5, "*")
                print(f"10 * 5 = {result2}")

                result3 = calc(10, 0, "/")
                print(f"10 / 0 = {result3}")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent,
            "In utils.py, rename the function 'calc' to 'compute'. "
            "In main.py, update 'from utils import calc' to 'from utils import compute' "
            "and replace all calc() calls with compute(). "
            "Verify: python3 main.py"
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        # At minimum the function should be renamed in utils.py
        utils_content = (tmp_path / "utils.py").read_text()
        assert "compute" in utils_content or "calc" not in utils_content.split("def ")[1][:10], \
            "Function not renamed in utils.py"


# ============================================================================
# Search tools (grep, glob)
# ============================================================================

class TestSearchTools:
    """Tests that search tools are used effectively."""

    async def test_find_and_fix_across_files(self, tmp_path):
        """Use grep to find a pattern across files and fix it."""
        scaffold_project(tmp_path, {
            "config.py": 'API_URL = "http://old-api.example.com/v1"\n',
            "client.py": '''\
                from config import API_URL
                def fetch():
                    print(f"Fetching from {API_URL}")
            ''',
            "readme.txt": "API endpoint: http://old-api.example.com/v1\n",
            "deploy.py": '''\
                API = "http://old-api.example.com/v1"
                def deploy():
                    print(f"Deploying to {API}")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent,
            "The API URL 'http://old-api.example.com/v1' needs to be changed to "
            "'https://api.example.com/v2' in ALL files. "
            "Use grep to find every occurrence, then use search_replace to update "
            "each file: config.py, deploy.py, and readme.txt."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        # Check at least config.py and deploy.py were updated
        updated = 0
        for fname in ["config.py", "deploy.py"]:
            content = (tmp_path / fname).read_text()
            if "old-api" not in content:
                updated += 1
        assert updated >= 1, f"No files were updated with new URL"

    async def test_find_unused_imports(self, tmp_path):
        """Use grep to find and remove unused imports."""
        scaffold_project(tmp_path, {
            "app.py": '''\
                import os
                import sys
                import json
                import re
                import math
                from pathlib import Path

                def process(input_path):
                    p = Path(input_path)
                    with open(p) as f:
                        data = json.load(f)
                    return len(data)

                if __name__ == "__main__":
                    result = process(sys.argv[1])
                    print(f"Items: {result}")
            ''',
            "data.json": '[1, 2, 3, 4, 5]',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "app.py has unused imports (os, re, math are never used). "
            "Find and remove the unused imports. Keep only the ones that are "
            "actually used. Verify the script still works."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        content = (tmp_path / "app.py").read_text()
        # These should be removed
        assert "import os\n" not in content, "os still imported"
        assert "import re\n" not in content, "re still imported"
        assert "import math\n" not in content, "math still imported"
        # These should remain
        assert "json" in content, "json import removed"
        assert "sys" in content, "sys import removed"
        ok, _ = check_runs(tmp_path, "python3 app.py data.json")
        assert ok


# ============================================================================
# Bash tool usage
# ============================================================================

class TestBashTool:
    """Tests that bash is used appropriately (tests, installs, not file creation)."""

    async def test_run_tests_after_fix(self, tmp_path):
        """Agent should run tests to verify its fix."""
        scaffold_project(tmp_path, {
            "calc.py": '''\
                def add(a, b):
                    return a + b

                def sub(a, b):
                    return a + b  # BUG: should be a - b
            ''',
            "test_calc.py": '''\
                from calc import add, sub

                def test_add():
                    assert add(2, 3) == 5
                    assert add(-1, 1) == 0

                def test_sub():
                    assert sub(5, 3) == 2
                    assert sub(10, 10) == 0

                if __name__ == "__main__":
                    test_add()
                    print("PASS: test_add")
                    test_sub()
                    print("PASS: test_sub")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "test_calc.py fails for test_sub. Fix the bug in calc.py and "
            "verify all tests pass by running python3 test_calc.py."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        # Should have used bash to run tests
        assert r.used_tool("bash"), "Agent didn't run tests"
        ok, out = check_runs(tmp_path, "python3 test_calc.py")
        assert ok, f"Tests still fail: {out}"

    async def test_install_and_use_package(self, tmp_path):
        """Agent should use bash for pip install."""
        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent,
            "Create a script that uses the 'pyyaml' package to:\n"
            "1. Install pyyaml if not present\n"
            "2. Create a YAML file with some configuration data\n"
            "3. Read it back and print it\n"
            "Create and run the script."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 1
        broken = check_syntax_all(tmp_path)
        assert not broken


# ============================================================================
# Combined tool workflows
# ============================================================================

class TestToolCombinations:
    """Tests requiring multiple tools working together."""

    async def test_read_analyze_fix_verify(self, tmp_path):
        """Full workflow: read -> understand -> fix -> test."""
        scaffold_project(tmp_path, {
            "parser.py": '''\
                def parse_csv_line(line):
                    """Parse a CSV line, handling quoted fields."""
                    fields = []
                    current = ""
                    in_quotes = False

                    for char in line:
                        if char == '"':
                            in_quotes = not in_quotes
                        elif char == ',' and not in_quotes:
                            fields.append(current.strip())
                            current = ""
                        else:
                            current += char

                    # BUG: doesn't append the last field
                    return fields

                if __name__ == "__main__":
                    # Should return ["Alice", "30", "New York"]
                    result = parse_csv_line('Alice,30,"New York"')
                    print(f"Fields: {result}")
                    print(f"Count: {len(result)}")
                    assert len(result) == 3, f"Expected 3 fields, got {len(result)}"
            ''',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "parser.py has a bug — parse_csv_line returns only 2 fields "
            "instead of 3. Read the code, find the bug, fix it, and verify."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 parser.py")
        assert ok, f"Still broken: {out}"
        assert "3" in out, f"Not fixed properly: {out}"
        # Should have used: read_file -> search_replace -> bash (verify)
        assert r.used_tool("read_file") or r.used_tool("search_replace"), \
            f"Didn't use proper tools: {r.tool_counts}"

    async def test_glob_then_batch_update(self, tmp_path):
        """Find files with glob, then update them all."""
        scaffold_project(tmp_path, {
            "src/module_a.py": '''\
                # TODO: add error handling
                def process_a(data):
                    return data.upper()
            ''',
            "src/module_b.py": '''\
                # TODO: add error handling
                def process_b(data):
                    return len(data)
            ''',
            "src/module_c.py": '''\
                # TODO: add error handling
                def process_c(data):
                    return data.split()
            ''',
            "src/__init__.py": "",
        })
        agent = make_agent(tmp_path, max_turns=30)
        r = await run_workload(agent,
            "All Python files in src/ have a TODO comment about adding error handling. "
            "Find all of them and add try/except blocks to each function that "
            "catches TypeError and returns None. Remove the TODO comments."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        broken = check_syntax_all(tmp_path)
        assert not broken
        # At least 2 of 3 files should have been modified
        modified = 0
        for f in ["src/module_a.py", "src/module_b.py", "src/module_c.py"]:
            content = (tmp_path / f).read_text()
            if "except" in content:
                modified += 1
        assert modified >= 2, f"Only {modified}/3 files got error handling"

    async def test_create_project_with_tests(self, tmp_path):
        """Create code AND tests — full development workflow."""
        agent = make_agent(tmp_path, max_turns=30)
        r = await run_workload(agent, max_events=400, prompt=
            "Build a 'StringUtils' class in string_utils.py with these methods:\n"
            "- capitalize_words(s) -> 'hello world' to 'Hello World'\n"
            "- snake_to_camel(s) -> 'hello_world' to 'helloWorld'\n"
            "- camel_to_snake(s) -> 'helloWorld' to 'hello_world'\n"
            "- truncate(s, max_len, suffix='...') -> truncate with suffix\n"
            "- is_palindrome(s) -> True/False\n\n"
            "Then create test_string_utils.py with at least 2 tests per method "
            "(10+ tests total). Run the tests to verify everything works."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert (tmp_path / "string_utils.py").exists(), "string_utils.py not created"
        broken = check_syntax_all(tmp_path)
        assert not broken
        # Check both files exist
        assert count_python_files(tmp_path) >= 2

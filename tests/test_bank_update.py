"""Test Bank: UPDATE — DryDock refactors and improves existing code.

15 tests across 3 difficulty levels. Each test provides working code
and asks DryDock to improve it (add features, refactor, add types, etc.)
without breaking existing functionality.

EASY (5): Add docstrings, type hints, simple features, 2-5 min each
MEDIUM (5): Refactor patterns, add error handling, extract modules, 5-15 min each
HARD (5): Major refactors, API changes, add test suites, 10-30 min each

Total estimated runtime: 2-4 hours.

Run: pytest tests/test_bank_update.py -v -s --timeout=1800
"""

from __future__ import annotations

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
# EASY: Simple improvements that shouldn't break anything
# ============================================================================

class TestUpdateEasy:

    async def test_add_type_hints(self, tmp_path):
        """Add type hints to untyped functions."""
        scaffold_project(tmp_path, {
            "mathlib.py": '''\
                def gcd(a, b):
                    while b:
                        a, b = b, a % b
                    return a

                def lcm(a, b):
                    return abs(a * b) // gcd(a, b)

                def is_prime(n):
                    if n < 2:
                        return False
                    for i in range(2, int(n**0.5) + 1):
                        if n % i == 0:
                            return False
                    return True

                def factorize(n):
                    factors = []
                    d = 2
                    while d * d <= n:
                        while n % d == 0:
                            factors.append(d)
                            n //= d
                        d += 1
                    if n > 1:
                        factors.append(n)
                    return factors
            ''',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "Add type hints to all functions in mathlib.py. "
            "Use int for numeric params, bool for is_prime return, list[int] for factorize. "
            "Don't change any logic. Verify syntax is valid."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        content = (tmp_path / "mathlib.py").read_text()
        assert "int" in content, "No type hints added"
        assert "-> bool" in content or "-> list" in content, "No return types"
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_add_docstrings(self, tmp_path):
        """Add docstrings to undocumented code."""
        scaffold_project(tmp_path, {
            "cache.py": '''\
                import time

                class LRUCache:
                    def __init__(self, capacity):
                        self.capacity = capacity
                        self.cache = {}
                        self.access_order = []

                    def get(self, key):
                        if key in self.cache:
                            self.access_order.remove(key)
                            self.access_order.append(key)
                            return self.cache[key]
                        return None

                    def put(self, key, value):
                        if key in self.cache:
                            self.access_order.remove(key)
                        elif len(self.cache) >= self.capacity:
                            oldest = self.access_order.pop(0)
                            del self.cache[oldest]
                        self.cache[key] = value
                        self.access_order.append(key)

                    def size(self):
                        return len(self.cache)

                    def clear(self):
                        self.cache.clear()
                        self.access_order.clear()
            ''',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "Add Google-style docstrings to the LRUCache class and all its methods "
            "in cache.py. Include Args, Returns, and a brief description. "
            "Don't change any logic."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        content = (tmp_path / "cache.py").read_text()
        assert '"""' in content, "No docstrings added"
        assert content.count('"""') >= 8, "Not all methods documented"
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_add_logging(self, tmp_path):
        """Add logging to existing code."""
        scaffold_project(tmp_path, {
            "processor.py": '''\
                import json

                def process_file(input_path, output_path):
                    with open(input_path) as f:
                        data = json.load(f)

                    results = []
                    for item in data:
                        if item.get("active"):
                            item["processed"] = True
                            results.append(item)

                    with open(output_path, "w") as f:
                        json.dump(results, f, indent=2)

                    return len(results)

                if __name__ == "__main__":
                    import sys
                    count = process_file(sys.argv[1], sys.argv[2])
                    print(f"Processed {count} items")
            ''',
            "data.json": '[{"name":"a","active":true},{"name":"b","active":false},{"name":"c","active":true}]',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "Add Python logging to processor.py:\n"
            "- Log at INFO when starting/finishing processing\n"
            "- Log at DEBUG for each item processed\n"
            "- Log at WARNING if input file is empty\n"
            "- Configure logging with format: '%(levelname)s - %(message)s'\n"
            "Don't change the processing logic. Verify it still works."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        content = (tmp_path / "processor.py").read_text()
        assert "logging" in content
        assert "import logging" in content
        broken = check_syntax_all(tmp_path)
        assert not broken
        ok, _ = check_runs(tmp_path, "python3 processor.py data.json out.json")
        assert ok

    async def test_add_cli_args(self, tmp_path):
        """Add argparse to a script that uses sys.argv."""
        scaffold_project(tmp_path, {
            "convert.py": '''\
                import sys
                import json
                import csv

                def csv_to_json(csv_path, json_path):
                    with open(csv_path) as f:
                        reader = csv.DictReader(f)
                        data = list(reader)
                    with open(json_path, "w") as f:
                        json.dump(data, f, indent=2)
                    return len(data)

                if __name__ == "__main__":
                    if len(sys.argv) != 3:
                        print("Usage: convert.py input.csv output.json")
                        sys.exit(1)
                    count = csv_to_json(sys.argv[1], sys.argv[2])
                    print(f"Converted {count} rows")
            ''',
            "test.csv": "name,age,city\nAlice,30,NYC\nBob,25,LA\n",
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "Replace the sys.argv handling in convert.py with argparse. "
            "Add a --help message, and add an optional --pretty flag "
            "for pretty-printed JSON (indent=2, default is compact). "
            "Verify: python3 convert.py test.csv out.json --pretty"
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        content = (tmp_path / "convert.py").read_text()
        assert "argparse" in content
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_add_error_handling(self, tmp_path):
        """Add proper error handling to a fragile script."""
        scaffold_project(tmp_path, {
            "fetcher.py": '''\
                import json
                import urllib.request

                def fetch_data(url):
                    response = urllib.request.urlopen(url)
                    return json.loads(response.read())

                def save_data(data, path):
                    with open(path, "w") as f:
                        json.dump(data, f)

                def process_api(url, output):
                    data = fetch_data(url)
                    items = data["results"]
                    processed = [{"id": i["id"], "name": i["name"]} for i in items]
                    save_data(processed, output)
                    return len(processed)

                if __name__ == "__main__":
                    import sys
                    count = process_api(sys.argv[1], sys.argv[2])
                    print(f"Saved {count} items")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "Add error handling to fetcher.py:\n"
            "- Handle network errors (URLError, TimeoutError)\n"
            "- Handle invalid JSON responses\n"
            "- Handle missing 'results' key in response\n"
            "- Handle file write errors\n"
            "- Print meaningful error messages, don't just crash\n"
            "- Return None on failure instead of crashing\n"
            "Don't change the happy path logic."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        content = (tmp_path / "fetcher.py").read_text()
        assert "except" in content, "No exception handling added"
        broken = check_syntax_all(tmp_path)
        assert not broken


# ============================================================================
# MEDIUM: Pattern-level refactoring
# ============================================================================

class TestUpdateMedium:

    async def test_refactor_to_classes(self, tmp_path):
        """Refactor procedural code to use classes."""
        scaffold_project(tmp_path, {
            "bank.py": '''\
                accounts = {}

                def create_account(account_id, name, initial_balance=0):
                    if account_id in accounts:
                        raise ValueError(f"Account {account_id} exists")
                    accounts[account_id] = {
                        "name": name, "balance": initial_balance, "transactions": []
                    }

                def deposit(account_id, amount):
                    if account_id not in accounts:
                        raise KeyError(f"Account {account_id} not found")
                    if amount <= 0:
                        raise ValueError("Amount must be positive")
                    accounts[account_id]["balance"] += amount
                    accounts[account_id]["transactions"].append(("deposit", amount))

                def withdraw(account_id, amount):
                    if account_id not in accounts:
                        raise KeyError(f"Account {account_id} not found")
                    if amount <= 0:
                        raise ValueError("Amount must be positive")
                    if accounts[account_id]["balance"] < amount:
                        raise ValueError("Insufficient funds")
                    accounts[account_id]["balance"] -= amount
                    accounts[account_id]["transactions"].append(("withdraw", amount))

                def get_balance(account_id):
                    if account_id not in accounts:
                        raise KeyError(f"Account {account_id} not found")
                    return accounts[account_id]["balance"]

                def transfer(from_id, to_id, amount):
                    withdraw(from_id, amount)
                    deposit(to_id, amount)

                if __name__ == "__main__":
                    create_account("001", "Alice", 1000)
                    create_account("002", "Bob", 500)
                    transfer("001", "002", 200)
                    print(f"Alice: ${get_balance('001')}")
                    print(f"Bob: ${get_balance('002')}")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent,
            "Refactor bank.py from procedural to object-oriented:\n"
            "- BankAccount class with deposit(), withdraw(), transfer_to()\n"
            "- Bank class that manages accounts\n"
            "- Keep the same __main__ behavior\n"
            "Verify the output is still: Alice: $800, Bob: $700"
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        broken = check_syntax_all(tmp_path)
        assert not broken
        ok, out = check_runs(tmp_path, "python3 bank.py")
        assert ok, f"Broken after refactor: {out}"
        assert "800" in out and "700" in out, f"Wrong output: {out}"

    async def test_extract_module(self, tmp_path):
        """Extract a utility module from a monolithic file."""
        scaffold_project(tmp_path, {
            "app.py": '''\
                import re
                import hashlib
                from datetime import datetime

                # --- Validation utilities (should be in utils.py) ---
                def validate_email(email):
                    pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+$"
                    return bool(re.match(pattern, email))

                def validate_password(password):
                    if len(password) < 8:
                        return False, "Too short"
                    if not any(c.isupper() for c in password):
                        return False, "Need uppercase"
                    if not any(c.isdigit() for c in password):
                        return False, "Need digit"
                    return True, "OK"

                def hash_password(password):
                    return hashlib.sha256(password.encode()).hexdigest()

                def format_date(dt):
                    return dt.strftime("%Y-%m-%d %H:%M")

                # --- Application logic ---
                users = []

                def register(name, email, password):
                    if not validate_email(email):
                        raise ValueError(f"Invalid email: {email}")
                    ok, msg = validate_password(password)
                    if not ok:
                        raise ValueError(f"Bad password: {msg}")
                    user = {
                        "name": name,
                        "email": email,
                        "password_hash": hash_password(password),
                        "created": format_date(datetime.now()),
                    }
                    users.append(user)
                    return user

                if __name__ == "__main__":
                    user = register("Alice", "alice@example.com", "Secret123")
                    print(f"Registered: {user['name']} ({user['email']})")
                    print(f"Created: {user['created']}")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent,
            "Refactor app.py:\n"
            "1. Extract validation and utility functions to a new utils.py\n"
            "2. Import them back into app.py\n"
            "3. Don't change the behavior\n"
            "Verify python3 app.py still works."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert (tmp_path / "utils.py").exists(), "utils.py not created"
        broken = check_syntax_all(tmp_path)
        assert not broken
        ok, out = check_runs(tmp_path, "python3 app.py")
        assert ok, f"Broken after refactor: {out}"
        assert "Registered" in out

    async def test_add_caching(self, tmp_path):
        """Add caching to slow functions."""
        scaffold_project(tmp_path, {
            "compute.py": '''\
                import time

                def fibonacci(n):
                    """Compute nth Fibonacci number (very slow for large n)."""
                    if n <= 1:
                        return n
                    return fibonacci(n - 1) + fibonacci(n - 2)

                def factorial(n):
                    """Compute n factorial."""
                    if n <= 1:
                        return 1
                    return n * factorial(n - 1)

                if __name__ == "__main__":
                    start = time.time()
                    print(f"fib(30) = {fibonacci(30)}")
                    elapsed = time.time() - start
                    print(f"Time: {elapsed:.2f}s")

                    print(f"fact(20) = {factorial(20)}")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "fibonacci(30) is very slow. Add memoization using functools.lru_cache "
            "to both fibonacci and factorial. Don't change the function signatures "
            "or return values. Verify fib(30) = 832040 and it runs fast (<1 second)."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        content = (tmp_path / "compute.py").read_text()
        assert "lru_cache" in content or "cache" in content
        ok, out = check_runs(tmp_path, "python3 compute.py")
        assert ok
        assert "832040" in out

    async def test_add_tests_to_existing(self, tmp_path):
        """Add a test suite to untested code."""
        scaffold_project(tmp_path, {
            "stack.py": '''\
                class Stack:
                    def __init__(self):
                        self._items = []

                    def push(self, item):
                        self._items.append(item)

                    def pop(self):
                        if self.is_empty():
                            raise IndexError("Pop from empty stack")
                        return self._items.pop()

                    def peek(self):
                        if self.is_empty():
                            raise IndexError("Peek at empty stack")
                        return self._items[-1]

                    def is_empty(self):
                        return len(self._items) == 0

                    def size(self):
                        return len(self._items)

                    def __repr__(self):
                        return f"Stack({self._items})"
            ''',
        })
        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent,
            "Create test_stack.py with comprehensive tests for Stack:\n"
            "- Test push, pop, peek\n"
            "- Test empty stack operations (should raise IndexError)\n"
            "- Test size tracking\n"
            "- Test multiple push/pop sequences\n"
            "- At least 8 test functions\n"
            "Run the tests to verify they all pass."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        test_file = tmp_path / "test_stack.py"
        assert test_file.exists(), "test_stack.py not created"
        broken = check_syntax_all(tmp_path)
        assert not broken
        ok, out = check_runs(tmp_path, "python3 test_stack.py")
        assert ok, f"Tests failed: {out}"

    async def test_make_async(self, tmp_path):
        """Convert synchronous code to async."""
        scaffold_project(tmp_path, {
            "downloader.py": '''\
                import time
                import random

                def fetch_url(url):
                    """Simulate fetching a URL (takes 1-2 seconds)."""
                    delay = random.uniform(0.1, 0.3)
                    time.sleep(delay)
                    return f"Content from {url} ({delay:.2f}s)"

                def download_all(urls):
                    """Download all URLs sequentially."""
                    results = []
                    for url in urls:
                        result = fetch_url(url)
                        results.append(result)
                    return results

                if __name__ == "__main__":
                    urls = [
                        "https://example.com/page1",
                        "https://example.com/page2",
                        "https://example.com/page3",
                        "https://example.com/page4",
                        "https://example.com/page5",
                    ]
                    start = time.time()
                    results = download_all(urls)
                    elapsed = time.time() - start
                    for r in results:
                        print(r)
                    print(f"Total time: {elapsed:.2f}s")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent,
            "Convert downloader.py to use async/await with asyncio:\n"
            "- fetch_url -> async using asyncio.sleep instead of time.sleep\n"
            "- download_all -> async using asyncio.gather for parallelism\n"
            "- Keep the same output format\n"
            "- The async version should be faster (parallel downloads)\n"
            "Verify it runs with python3 downloader.py"
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        content = (tmp_path / "downloader.py").read_text()
        assert "async" in content, "Not converted to async"
        assert "asyncio" in content
        broken = check_syntax_all(tmp_path)
        assert not broken
        ok, out = check_runs(tmp_path, "python3 downloader.py")
        assert ok, f"Async version broken: {out}"


# ============================================================================
# HARD: Major refactoring and feature additions
# ============================================================================

class TestUpdateHard:

    async def test_add_plugin_system(self, tmp_path):
        """Add a plugin system to an existing application."""
        scaffold_project(tmp_path, {
            "textool/__init__.py": "",
            "textool/core.py": '''\
                class TextProcessor:
                    def process(self, text):
                        return text

                    def uppercase(self, text):
                        return text.upper()

                    def lowercase(self, text):
                        return text.lower()

                    def reverse(self, text):
                        return text[::-1]

                    def word_count(self, text):
                        return len(text.split())
            ''',
            "textool/cli.py": '''\
                import sys
                from textool.core import TextProcessor

                def main():
                    if len(sys.argv) < 3:
                        print("Usage: python3 -m textool <command> <text>")
                        print("Commands: upper, lower, reverse, wordcount")
                        sys.exit(1)

                    cmd = sys.argv[1]
                    text = " ".join(sys.argv[2:])
                    proc = TextProcessor()

                    commands = {
                        "upper": proc.uppercase,
                        "lower": proc.lowercase,
                        "reverse": proc.reverse,
                        "wordcount": proc.word_count,
                    }

                    if cmd not in commands:
                        print(f"Unknown command: {cmd}")
                        sys.exit(1)

                    result = commands[cmd](text)
                    print(result)

                if __name__ == "__main__":
                    main()
            ''',
            "textool/__main__.py": "from textool.cli import main\nmain()\n",
        })
        agent = make_agent(tmp_path, max_turns=30)
        r = await run_workload(agent, max_events=400, prompt=
            "Add a plugin system to the textool package:\n"
            "1. Create textool/plugins.py with a PluginManager class\n"
            "2. Plugins are Python files in a 'plugins/' directory\n"
            "3. Each plugin defines a 'commands' dict mapping name -> function\n"
            "4. The CLI auto-discovers and loads plugins\n"
            "5. Create a sample plugin plugins/caesar.py that adds a 'caesar' command "
            "(Caesar cipher with shift=3)\n"
            "6. Update cli.py to load plugins\n"
            "Existing commands must still work. Test both built-in and plugin commands."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 4
        broken = check_syntax_all(tmp_path)
        assert not broken
        # Built-in commands should still work
        ok, out = check_runs(tmp_path, "python3 -m textool upper hello")
        assert ok, f"Built-in command broken: {out}"

    async def test_add_middleware_pipeline(self, tmp_path):
        """Add middleware support to a request handler."""
        scaffold_project(tmp_path, {
            "server/__init__.py": "",
            "server/handler.py": '''\
                import json
                import time

                class Request:
                    def __init__(self, method, path, headers=None, body=""):
                        self.method = method
                        self.path = path
                        self.headers = headers or {}
                        self.body = body
                        self.context = {}

                class Response:
                    def __init__(self, status=200, body="", headers=None):
                        self.status = status
                        self.body = body
                        self.headers = headers or {}

                    @classmethod
                    def json(cls, data, status=200):
                        return cls(status, json.dumps(data), {"Content-Type": "application/json"})

                class RequestHandler:
                    def __init__(self):
                        self.routes = {}

                    def route(self, method, path):
                        def decorator(func):
                            self.routes[(method, path)] = func
                            return func
                        return decorator

                    def handle(self, request):
                        handler = self.routes.get((request.method, request.path))
                        if not handler:
                            return Response(404, "Not Found")
                        return handler(request)
            ''',
            "demo.py": '''\
                from server.handler import Request, Response, RequestHandler

                app = RequestHandler()

                @app.route("GET", "/")
                def index(req):
                    return Response.json({"message": "Hello!"})

                @app.route("GET", "/users")
                def users(req):
                    return Response.json({"users": ["Alice", "Bob"]})

                # Test
                req = Request("GET", "/")
                resp = app.handle(req)
                print(f"GET / -> {resp.status}: {resp.body}")

                req = Request("GET", "/users")
                resp = app.handle(req)
                print(f"GET /users -> {resp.status}: {resp.body}")

                req = Request("GET", "/missing")
                resp = app.handle(req)
                print(f"GET /missing -> {resp.status}: {resp.body}")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=30)
        r = await run_workload(agent, max_events=400, prompt=
            "Add a middleware pipeline to the server:\n"
            "1. Create server/middleware.py with middleware support\n"
            "2. Middleware functions take (request, next) and return a response\n"
            "3. They can modify the request before passing to next(request)\n"
            "4. They can modify the response after getting it from next(request)\n"
            "5. Create 3 middleware: logging (print request/response), "
            "timing (add X-Response-Time header), auth (check Authorization header)\n"
            "6. Update handler.py to support app.use(middleware)\n"
            "7. Update demo.py to use all 3 middleware\n"
            "Verify demo.py still works with middleware applied."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        broken = check_syntax_all(tmp_path)
        assert not broken
        ok, out = check_runs(tmp_path, "python3 demo.py")
        assert ok, f"Broken after adding middleware: {out}"

    async def test_add_persistence(self, tmp_path):
        """Add database persistence to an in-memory application."""
        scaffold_project(tmp_path, {
            "bookshelf.py": '''\
                class Book:
                    def __init__(self, title, author, year, isbn):
                        self.title = title
                        self.author = author
                        self.year = year
                        self.isbn = isbn
                        self.rating = None
                        self.notes = ""

                    def __str__(self):
                        star = f" [{self.rating}/5]" if self.rating else ""
                        return f"{self.title} by {self.author} ({self.year}){star}"

                class Bookshelf:
                    def __init__(self):
                        self.books = []

                    def add(self, title, author, year, isbn):
                        book = Book(title, author, year, isbn)
                        self.books.append(book)
                        return book

                    def find(self, query):
                        query = query.lower()
                        return [b for b in self.books
                                if query in b.title.lower() or query in b.author.lower()]

                    def rate(self, isbn, rating):
                        for b in self.books:
                            if b.isbn == isbn:
                                b.rating = rating
                                return b
                        return None

                    def list_all(self):
                        return sorted(self.books, key=lambda b: b.title)

                if __name__ == "__main__":
                    shelf = Bookshelf()
                    shelf.add("The Pragmatic Programmer", "Hunt & Thomas", 1999, "978-0135957059")
                    shelf.add("Clean Code", "Robert Martin", 2008, "978-0132350884")
                    shelf.add("Design Patterns", "Gang of Four", 1994, "978-0201633610")
                    shelf.rate("978-0132350884", 4)

                    print("All books:")
                    for b in shelf.list_all():
                        print(f"  {b}")
                    print(f"\\nSearch 'code': {[str(b) for b in shelf.find('code')]}")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=35)
        r = await run_workload(agent, max_events=400, prompt=
            "Add SQLite persistence to bookshelf.py:\n"
            "1. Bookshelf.__init__ takes an optional db_path parameter\n"
            "2. Books are stored in SQLite, not just in memory\n"
            "3. All methods (add, find, rate, list_all) work with the database\n"
            "4. Data persists between runs\n"
            "5. Keep the same __main__ behavior but use a database file\n"
            "Verify it works by running twice — second run should show existing books."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        broken = check_syntax_all(tmp_path)
        assert not broken
        ok1, out1 = check_runs(tmp_path, "python3 bookshelf.py")
        assert ok1, f"First run failed: {out1}"

    async def test_convert_to_package(self, tmp_path):
        """Convert a single-file script into a proper Python package."""
        scaffold_project(tmp_path, {
            "analyzer.py": '''\
                import json
                import csv
                import sys
                import os
                from collections import Counter
                from datetime import datetime

                def load_data(path):
                    ext = os.path.splitext(path)[1]
                    if ext == ".json":
                        with open(path) as f:
                            return json.load(f)
                    elif ext == ".csv":
                        with open(path) as f:
                            return list(csv.DictReader(f))
                    else:
                        raise ValueError(f"Unsupported: {ext}")

                def summarize(data, group_by=None):
                    if group_by:
                        groups = {}
                        for row in data:
                            key = row.get(group_by, "Unknown")
                            groups.setdefault(key, []).append(row)
                        return {k: len(v) for k, v in groups.items()}
                    return {"total_records": len(data)}

                def find_duplicates(data, key):
                    seen = Counter(str(row.get(key)) for row in data)
                    return {k: v for k, v in seen.items() if v > 1}

                def validate(data, required_fields):
                    invalid = []
                    for i, row in enumerate(data):
                        missing = [f for f in required_fields if not row.get(f)]
                        if missing:
                            invalid.append({"row": i, "missing": missing})
                    return invalid

                def export(data, path, format="json"):
                    if format == "json":
                        with open(path, "w") as f:
                            json.dump(data, f, indent=2)
                    elif format == "csv":
                        with open(path, "w", newline="") as f:
                            if data:
                                w = csv.DictWriter(f, fieldnames=data[0].keys())
                                w.writeheader()
                                w.writerows(data)

                if __name__ == "__main__":
                    if len(sys.argv) < 2:
                        print("Usage: analyzer.py <file> [--group-by FIELD]")
                        sys.exit(1)
                    data = load_data(sys.argv[1])
                    print(json.dumps(summarize(data), indent=2))
            ''',
            "sample.json": json.dumps([
                {"id": 1, "name": "A", "type": "x"},
                {"id": 2, "name": "B", "type": "y"},
                {"id": 3, "name": "C", "type": "x"},
            ]),
        })
        agent = make_agent(tmp_path, max_turns=35)
        r = await run_workload(agent, max_events=400, prompt=
            "Convert the single-file analyzer.py into a proper Python package:\n"
            "1. Create analyzer/ package directory\n"
            "2. Split into: loader.py, summarizer.py, validator.py, exporter.py\n"
            "3. Create analyzer/__init__.py with clean public API\n"
            "4. Create analyzer/__main__.py for CLI (python3 -m analyzer)\n"
            "5. Use argparse with subcommands: summarize, validate, export\n"
            "6. Delete the original analyzer.py\n"
            "Verify: python3 -m analyzer summarize sample.json"
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 4, "Not enough files in package"
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_add_concurrent_processing(self, tmp_path):
        """Add concurrent processing to a sequential batch processor."""
        scaffold_project(tmp_path, {
            "batch.py": '''\
                import time
                import hashlib
                import json

                def process_item(item):
                    """Simulate processing an item (CPU-bound work)."""
                    # Simulate work with hashing
                    data = json.dumps(item)
                    for _ in range(1000):
                        data = hashlib.md5(data.encode()).hexdigest()
                    return {"id": item["id"], "hash": data, "processed": True}

                def process_batch(items):
                    """Process items sequentially."""
                    results = []
                    for item in items:
                        result = process_item(item)
                        results.append(result)
                    return results

                if __name__ == "__main__":
                    items = [{"id": i, "data": f"item_{i}"} for i in range(20)]

                    start = time.time()
                    results = process_batch(items)
                    elapsed = time.time() - start

                    print(f"Processed {len(results)} items in {elapsed:.2f}s")
                    print(f"First result: {results[0]}")
                    print(f"Last result: {results[-1]}")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent, max_events=300, prompt=
            "Add concurrent processing to batch.py:\n"
            "1. Add a process_batch_parallel(items, workers=4) function\n"
            "2. Use concurrent.futures.ProcessPoolExecutor\n"
            "3. Update __main__ to compare sequential vs parallel times\n"
            "4. Show speedup ratio\n"
            "Don't change process_item(). Verify by running it."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        content = (tmp_path / "batch.py").read_text()
        assert "concurrent" in content or "ProcessPool" in content or "ThreadPool" in content
        broken = check_syntax_all(tmp_path)
        assert not broken
        ok, out = check_runs(tmp_path, "python3 batch.py", timeout=60)
        assert ok, f"Concurrent version broken: {out}"

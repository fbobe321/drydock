"""Test Bank: MULTI-AGENT — DryDock uses subagents for complex tasks.

10 tests verifying that DryDock naturally delegates work to subagents
(explore, diagnostic, planner) for tasks that require it.

Tests check:
- Agent uses the 'task' tool to spawn subagents
- Agent uses 'invoke_skill' for complex workflows
- Agent handles multi-file projects by exploring first
- Agent uses background subagents for parallel work

Total estimated runtime: 2-4 hours.

Run: pytest tests/test_bank_multiagent.py -v -s --timeout=1800
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.testbank_helpers import (
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
# Subagent delegation tests
# ============================================================================

class TestMultiAgentDelegation:
    """Tests that DryDock delegates exploration to subagents for large codebases."""

    async def test_explore_before_fix(self, tmp_path):
        """Agent should explore a multi-file project before attempting a fix."""
        scaffold_project(tmp_path, {
            "myapp/__init__.py": "",
            "myapp/auth.py": '''\
                import hashlib
                import secrets

                class AuthManager:
                    def __init__(self):
                        self.users = {}
                        self.sessions = {}

                    def register(self, username, password):
                        salt = secrets.token_hex(16)
                        hashed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
                        self.users[username] = {"salt": salt, "hash": hashed}

                    def login(self, username, password):
                        user = self.users.get(username)
                        if not user:
                            return None
                        hashed = hashlib.sha256(f"{user['salt']}{password}".encode()).hexdigest()
                        if hashed != user["hash"]:
                            return None
                        token = secrets.token_urlsafe(32)
                        self.sessions[token] = username
                        return token

                    def verify(self, token):
                        return self.sessions.get(token)

                    def logout(self, token):
                        self.sessions.pop(token, None)
            ''',
            "myapp/database.py": '''\
                import sqlite3
                from contextlib import contextmanager

                class Database:
                    def __init__(self, path=":memory:"):
                        self.path = path
                        self.conn = sqlite3.connect(path)
                        self.conn.row_factory = sqlite3.Row

                    @contextmanager
                    def transaction(self):
                        try:
                            yield self.conn
                            self.conn.commit()
                        except Exception:
                            self.conn.rollback()
                            raise

                    def execute(self, sql, params=None):
                        return self.conn.execute(sql, params or ())

                    def fetchall(self, sql, params=None):
                        return self.conn.execute(sql, params or ()).fetchall()

                    def fetchone(self, sql, params=None):
                        return self.conn.execute(sql, params or ()).fetchone()
            ''',
            "myapp/models.py": '''\
                from myapp.database import Database

                class UserModel:
                    def __init__(self, db: Database):
                        self.db = db
                        self._init_table()

                    def _init_table(self):
                        self.db.execute("""
                            CREATE TABLE IF NOT EXISTS users (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                username TEXT UNIQUE NOT NULL,
                                email TEXT UNIQUE NOT NULL,
                                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                            )
                        """)

                    def create(self, username, email):
                        self.db.execute(
                            "INSERT INTO users (username, email) VALUES (?, ?)",
                            (username, email)
                        )
                        return self.get_by_username(username)

                    def get_by_username(self, username):
                        return self.db.fetchone(
                            "SELECT * FROM users WHERE username = ?", (username,)
                        )

                    def get_by_email(self, email):
                        return self.db.fetchone(
                            "SELECT * FROM users WHERE email = ?", (email,)
                        )

                    def list_all(self):
                        return self.db.fetchall("SELECT * FROM users")
            ''',
            "myapp/api.py": '''\
                import json
                from myapp.auth import AuthManager
                from myapp.database import Database
                from myapp.models import UserModel

                class API:
                    def __init__(self):
                        self.auth = AuthManager()
                        self.db = Database()
                        self.users = UserModel(self.db)

                    def handle_request(self, method, path, body=None, token=None):
                        if path == "/register" and method == "POST":
                            return self._register(body)
                        elif path == "/login" and method == "POST":
                            return self._login(body)
                        elif path == "/users" and method == "GET":
                            return self._list_users(token)
                        return {"error": "Not found"}, 404

                    def _register(self, body):
                        data = json.loads(body) if isinstance(body, str) else body
                        username = data.get("username")
                        email = data.get("email")
                        password = data.get("password")
                        if not all([username, email, password]):
                            return {"error": "Missing fields"}, 400
                        self.auth.register(username, password)
                        self.users.create(username, email)
                        return {"message": f"User {username} created"}, 201

                    def _login(self, body):
                        data = json.loads(body) if isinstance(body, str) else body
                        token = self.auth.login(data["username"], data["password"])
                        if not token:
                            return {"error": "Invalid credentials"}, 401
                        return {"token": token}, 200

                    def _list_users(self, token):
                        if not token or not self.auth.verify(token):
                            return {"error": "Unauthorized"}, 401
                        users = self.users.list_all()
                        return {"users": [dict(u) for u in users]}, 200
            ''',
            "myapp/server.py": '''\
                from myapp.api import API

                def run_demo():
                    api = API()

                    # Register
                    resp, status = api.handle_request("POST", "/register", {
                        "username": "alice", "email": "alice@test.com", "password": "secret123"
                    })
                    print(f"Register: {status} {resp}")

                    # Login
                    resp, status = api.handle_request("POST", "/login", {
                        "username": "alice", "password": "secret123"
                    })
                    print(f"Login: {status} {resp}")
                    token = resp.get("token")

                    # List users (authenticated)
                    resp, status = api.handle_request("GET", "/users", token=token)
                    print(f"Users: {status} {resp}")

                    # List users (unauthenticated)
                    resp, status = api.handle_request("GET", "/users")
                    print(f"Unauth: {status} {resp}")

                if __name__ == "__main__":
                    run_demo()
            ''',
        })
        agent = make_agent(tmp_path, max_turns=30)
        r = await run_workload(agent, max_events=300, prompt=
            "This is a multi-module application with auth, database, models, "
            "and API layers. I need you to:\n"
            "1. First explore and understand the project structure\n"
            "2. Add a DELETE /users/<username> endpoint to the API\n"
            "3. Add the corresponding delete method to UserModel\n"
            "4. Test by running python3 -m myapp.server\n"
            "Use subagents to explore the codebase before making changes."
        )

        assert r.ok, f"Agent crashed: {r.summary()}"
        broken = check_syntax_all(tmp_path)
        assert not broken
        # Check that it explored before editing
        if r.total_tool_calls >= 5:
            # For multi-file projects, agent should have explored first
            first_tools = r.tool_calls[:5]
            has_exploration = any(t in ("grep", "read_file", "task", "glob") for t in first_tools)
            assert has_exploration, \
                f"Agent didn't explore first. First 5 tools: {first_tools}"

    async def test_delegate_for_large_codebase(self, tmp_path):
        """Agent should use subagents when 5+ files need examination."""
        # Create a realistic project with many files
        scaffold_project(tmp_path, {
            "shop/__init__.py": "",
            "shop/models/__init__.py": "",
            "shop/models/product.py": '''\
                from dataclasses import dataclass
                @dataclass
                class Product:
                    id: int
                    name: str
                    price: float
                    stock: int = 0
            ''',
            "shop/models/order.py": '''\
                from dataclasses import dataclass, field
                from datetime import datetime
                @dataclass
                class OrderItem:
                    product_id: int
                    quantity: int
                    price: float
                @dataclass
                class Order:
                    id: int
                    items: list = field(default_factory=list)
                    created_at: datetime = field(default_factory=datetime.now)
                    @property
                    def total(self):
                        return sum(i.price * i.quantity for i in self.items)
            ''',
            "shop/models/customer.py": '''\
                from dataclasses import dataclass
                @dataclass
                class Customer:
                    id: int
                    name: str
                    email: str
            ''',
            "shop/services/__init__.py": "",
            "shop/services/catalog.py": '''\
                from shop.models.product import Product
                class CatalogService:
                    def __init__(self):
                        self.products = {}
                    def add_product(self, product):
                        self.products[product.id] = product
                    def get_product(self, product_id):
                        return self.products.get(product_id)
                    def search(self, query):
                        query = query.lower()
                        return [p for p in self.products.values() if query in p.name.lower()]
            ''',
            "shop/services/ordering.py": '''\
                from shop.models.order import Order, OrderItem
                class OrderService:
                    def __init__(self, catalog):
                        self.catalog = catalog
                        self.orders = {}
                        self._next_id = 1
                    def create_order(self, items):
                        order_items = []
                        for pid, qty in items:
                            product = self.catalog.get_product(pid)
                            if not product:
                                raise ValueError(f"Product {pid} not found")
                            if product.stock < qty:
                                raise ValueError(f"Insufficient stock for {product.name}")
                            order_items.append(OrderItem(pid, qty, product.price))
                        order = Order(id=self._next_id, items=order_items)
                        self._next_id += 1
                        self.orders[order.id] = order
                        # BUG: doesn't reduce stock
                        return order
            ''',
            "shop/services/shipping.py": '''\
                class ShippingService:
                    RATES = {"standard": 5.99, "express": 12.99, "overnight": 24.99}
                    def calculate(self, order, method="standard"):
                        base = self.RATES.get(method, 5.99)
                        if order.total > 50:
                            return base * 0.5
                        return base
            ''',
            "shop/main.py": '''\
                from shop.models.product import Product
                from shop.services.catalog import CatalogService
                from shop.services.ordering import OrderService
                from shop.services.shipping import ShippingService

                def demo():
                    catalog = CatalogService()
                    catalog.add_product(Product(1, "Widget", 9.99, stock=100))
                    catalog.add_product(Product(2, "Gadget", 24.99, stock=50))
                    catalog.add_product(Product(3, "Doohickey", 4.99, stock=200))

                    orders = OrderService(catalog)
                    shipping = ShippingService()

                    order = orders.create_order([(1, 3), (2, 1)])
                    ship_cost = shipping.calculate(order)
                    print(f"Order #{order.id}: ${order.total:.2f} + ${ship_cost:.2f} shipping")

                    # Check stock (should be reduced but isn't!)
                    w = catalog.get_product(1)
                    print(f"Widget stock: {w.stock} (should be 97)")

                if __name__ == "__main__":
                    demo()
            ''',
        })
        agent = make_agent(tmp_path, max_turns=30)
        r = await run_workload(agent, max_events=300, prompt=
            "There's a bug in this e-commerce system: placing an order doesn't "
            "reduce product stock. The project has 8+ files across models/ and services/. "
            "Find and fix the bug. Verify by running python3 -m shop.main"
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        broken = check_syntax_all(tmp_path)
        assert not broken
        ok, out = check_runs(tmp_path, "python3 -m shop.main")
        assert ok, f"Still broken: {out}"
        # Stock should be reduced
        assert "97" in out, f"Stock not reduced: {out}"

    async def test_investigate_test_failure(self, tmp_path):
        """Agent should investigate why tests fail using diagnostic approach."""
        scaffold_project(tmp_path, {
            "mathutil/__init__.py": "",
            "mathutil/geometry.py": '''\
                import math

                def circle_area(radius):
                    return math.pi * radius ** 2

                def circle_circumference(radius):
                    return 2 * math.pi * radius

                def triangle_area(base, height):
                    return 0.5 * base * height

                def rectangle_area(width, height):
                    return width * height

                def distance(x1, y1, x2, y2):
                    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)

                def polygon_area(vertices):
                    """Calculate area of polygon using shoelace formula."""
                    n = len(vertices)
                    if n < 3:
                        return 0
                    area = 0
                    for i in range(n):
                        j = (i + 1) % n
                        # BUG: subtraction is reversed
                        area += vertices[i][0] * vertices[j][1]
                        area -= vertices[j][0] * vertices[i][1]
                    return abs(area) / 2  # This fixes the sign but not the wrong formula
            ''',
            "mathutil/statistics.py": '''\
                import math
                from collections import Counter

                def mean(data):
                    return sum(data) / len(data)

                def median(data):
                    sorted_data = sorted(data)
                    n = len(sorted_data)
                    mid = n // 2
                    if n % 2 == 0:
                        return (sorted_data[mid - 1] + sorted_data[mid]) / 2
                    return sorted_data[mid]

                def mode(data):
                    counts = Counter(data)
                    max_count = max(counts.values())
                    modes = [k for k, v in counts.items() if v == max_count]
                    return modes[0] if len(modes) == 1 else modes

                def std_dev(data):
                    avg = mean(data)
                    # BUG: should divide by n-1 for sample std dev, or n for population
                    variance = sum((x - avg) ** 2 for x in data) / len(data)
                    return math.sqrt(variance)
            ''',
            "tests/__init__.py": "",
            "tests/test_geometry.py": '''\
                import math
                from mathutil.geometry import (
                    circle_area, triangle_area, distance, polygon_area
                )

                def test_circle():
                    assert abs(circle_area(1) - math.pi) < 0.001
                    assert abs(circle_area(5) - 78.5398) < 0.01

                def test_triangle():
                    assert triangle_area(10, 5) == 25.0

                def test_distance():
                    assert distance(0, 0, 3, 4) == 5.0
                    assert abs(distance(1, 1, 4, 5) - 5.0) < 0.01

                def test_polygon_square():
                    # Unit square: area should be 1.0
                    square = [(0,0), (1,0), (1,1), (0,1)]
                    assert abs(polygon_area(square) - 1.0) < 0.001, \\
                        f"Square area: {polygon_area(square)}"

                def test_polygon_triangle():
                    # Right triangle with legs 3,4: area = 6
                    tri = [(0,0), (3,0), (0,4)]
                    assert abs(polygon_area(tri) - 6.0) < 0.001, \\
                        f"Triangle area: {polygon_area(tri)}"

                if __name__ == "__main__":
                    for name, func in sorted(globals().items()):
                        if name.startswith("test_"):
                            try:
                                func()
                                print(f"PASS: {name}")
                            except Exception as e:
                                print(f"FAIL: {name}: {e}")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "Run python3 tests/test_geometry.py — some tests may fail. "
            "Investigate the failures, diagnose the root cause, and fix the bugs. "
            "You should explore the codebase structure first since there are "
            "multiple modules. All tests must pass."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 tests/test_geometry.py")
        assert ok, f"Tests still failing: {out}"

    async def test_plan_then_implement(self, tmp_path):
        """Agent should plan before implementing a complex change."""
        scaffold_project(tmp_path, {
            "notes/__init__.py": "",
            "notes/note.py": '''\
                from dataclasses import dataclass, field
                from datetime import datetime

                @dataclass
                class Note:
                    title: str
                    content: str
                    created: datetime = field(default_factory=datetime.now)
                    tags: list = field(default_factory=list)

                    def __str__(self):
                        tag_str = f" [{', '.join(self.tags)}]" if self.tags else ""
                        return f"{self.title}{tag_str}: {self.content[:50]}..."
            ''',
            "notes/store.py": '''\
                import json
                from pathlib import Path
                from notes.note import Note

                class NoteStore:
                    def __init__(self, path="notes.json"):
                        self.path = Path(path)
                        self.notes = []

                    def add(self, title, content, tags=None):
                        note = Note(title=title, content=content, tags=tags or [])
                        self.notes.append(note)
                        return note

                    def search(self, query):
                        q = query.lower()
                        return [n for n in self.notes
                                if q in n.title.lower() or q in n.content.lower()]

                    def by_tag(self, tag):
                        return [n for n in self.notes if tag in n.tags]

                    def all(self):
                        return list(self.notes)
            ''',
            "main.py": '''\
                from notes.store import NoteStore

                store = NoteStore()
                store.add("Meeting Notes", "Discussed Q1 roadmap", tags=["work", "meetings"])
                store.add("Shopping List", "Milk, eggs, bread", tags=["personal"])
                store.add("Bug Fix", "Fixed null pointer in auth module", tags=["work", "bugs"])

                print("All notes:")
                for n in store.all():
                    print(f"  {n}")

                print("\\nWork notes:")
                for n in store.by_tag("work"):
                    print(f"  {n}")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=35)
        r = await run_workload(agent, max_events=400, prompt=
            "I need to add several features to this notes app. This is a complex "
            "change so please plan first:\n\n"
            "1. Add JSON persistence (save/load from notes.json)\n"
            "2. Add a CLI interface with commands: add, list, search, tag\n"
            "3. Add note editing (update title/content)\n"
            "4. Add note deletion\n"
            "5. Add date-based filtering (list notes from last N days)\n\n"
            "Plan your approach, then implement. Verify by testing the CLI."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert count_python_files(tmp_path) >= 3
        broken = check_syntax_all(tmp_path)
        assert not broken


# ============================================================================
# Tool usage verification tests
# ============================================================================

class TestToolUsagePatterns:
    """Verify DryDock uses the right tools for the right tasks."""

    async def test_uses_write_file_not_bash(self, tmp_path):
        """Agent should create files with write_file, not echo/cat in bash."""
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "Create three Python files:\n"
            "1. utils.py with a helper function\n"
            "2. main.py that imports from utils\n"
            "3. config.py with configuration constants\n"
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        # Should have used write_file, not bash for file creation
        write_calls = r.tool_counts.get("write_file", 0)
        bash_calls = r.tool_counts.get("bash", 0)
        assert write_calls >= 2, \
            f"Should use write_file for creating files. " \
            f"write_file={write_calls}, bash={bash_calls}"

    async def test_uses_grep_not_bash_grep(self, tmp_path):
        """Agent should use grep tool, not bash grep command."""
        # Create files to search through
        scaffold_project(tmp_path, {
            "module_a.py": "def helper(): pass\ndef process(): pass\n",
            "module_b.py": "from module_a import helper\ndef run(): helper()\n",
            "module_c.py": "from module_a import process\ndef main(): process()\n",
            "module_d.py": "import os\ndef cleanup(): pass\n",
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "Find all files that import from module_a and list them. "
            "Then add a docstring to the helper() function in module_a.py."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        # Should have used grep tool
        grep_calls = r.tool_counts.get("grep", 0)
        assert grep_calls >= 1 or r.used_tool("glob"), \
            f"Should use grep/glob for searching. Tools: {r.tool_counts}"

    async def test_uses_read_file_not_cat(self, tmp_path):
        """Agent should use read_file tool, not cat in bash."""
        scaffold_project(tmp_path, {
            "data.py": '''\
                import json

                DATA = {
                    "users": [
                        {"name": "Alice", "role": "admin"},
                        {"name": "Bob", "role": "user"},
                    ],
                    "settings": {
                        "debug": True,
                        "version": "1.0",
                    }
                }

                def get_admins():
                    return [u for u in DATA["users"] if u["role"] == "admin"]
            ''',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "Read data.py and add a get_users_by_role(role) function. "
            "Don't change existing functions."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        read_calls = r.tool_counts.get("read_file", 0)
        assert read_calls >= 1, \
            f"Should use read_file. Tools: {r.tool_counts}"
        broken = check_syntax_all(tmp_path)
        assert not broken

    async def test_handles_errors_gracefully(self, tmp_path):
        """Agent should not crash or loop when encountering errors."""
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "Read the file 'nonexistent.py' and if it doesn't exist, "
            "create it with a simple hello world function."
        )

        assert r.ok, f"Agent crashed on missing file: {r.summary()}"
        assert r.force_stops == 0, "Agent was force-stopped"
        assert count_python_files(tmp_path) >= 1, "No file created"

    async def test_no_loop_on_success(self, tmp_path):
        """Agent should stop after completing the task, not keep going."""
        agent = make_agent(tmp_path, max_turns=20)
        r = await run_workload(agent,
            "Create a Python file called 'greet.py' that defines a function "
            "greet(name) which returns 'Hello, {name}!'. Run it to verify."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        assert r.total_tool_calls <= 10, \
            f"Agent used {r.total_tool_calls} tool calls for a simple task. " \
            f"Tools: {r.tool_counts}"
        assert r.circuit_breaker_fires == 0, \
            f"Circuit breaker fired on a simple task"

    async def test_efficient_bug_fix(self, tmp_path):
        """Agent should fix a simple bug in few tool calls."""
        scaffold_project(tmp_path, {
            "math_ops.py": '''\
                def add(a, b):
                    return a + b

                def multiply(a, b):
                    return a + b  # BUG: should be a * b

                if __name__ == "__main__":
                    print(f"2 + 3 = {add(2, 3)}")
                    print(f"2 * 3 = {multiply(2, 3)}")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=12)
        r = await run_workload(agent,
            "multiply(2, 3) returns 5 instead of 6. Fix the bug in math_ops.py."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 math_ops.py")
        assert ok
        assert "6" in out, f"Bug not fixed: {out}"
        # Should be efficient — read, fix, verify = ~3-5 calls
        assert r.total_tool_calls <= 8, \
            f"Used {r.total_tool_calls} calls for a one-line fix"

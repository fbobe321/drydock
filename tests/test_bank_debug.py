"""Test Bank: DEBUG — DryDock finds and fixes real bugs.

20 tests across 3 difficulty levels. Each test provides a buggy project
and asks DryDock to find and fix the bug. Verified by running the tests.

EASY (8): Single bug, obvious error messages, 2-5 min each
MEDIUM (7): Subtle bugs, requires reading code carefully, 5-15 min each
HARD (5): Multi-file bugs, architectural issues, 10-30 min each

Total estimated runtime: 2-5 hours.

Run: pytest tests/test_bank_debug.py -v -s --timeout=1800
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.testbank_helpers import (
    check_runs,
    check_syntax_all,
    make_agent,
    requires_vllm,
    run_workload,
    scaffold_buggy_project,
    scaffold_project,
)

pytestmark = [requires_vllm, pytest.mark.asyncio]


# ============================================================================
# EASY: Single obvious bugs (2-5 min each)
# ============================================================================

class TestDebugEasy:
    """Simple bugs with clear error messages."""

    async def test_fix_syntax_error(self, tmp_path):
        """Fix a missing colon on a function definition."""
        scaffold_buggy_project(tmp_path, "calculator_syntax")
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "calculator.py has a syntax error. Find and fix it. "
            "Verify by running: python3 calculator.py"
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 calculator.py")
        assert ok, f"Still broken: {out}"

    async def test_fix_import_typo(self, tmp_path):
        """Fix a typo in an import statement."""
        scaffold_buggy_project(tmp_path, "import_chain")
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "python3 main.py crashes with an import error. Find and fix the bug. "
            "Verify it runs correctly."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 main.py")
        assert ok, f"Still broken: {out}"
        assert "Alice" in out, f"Expected 'Alice' in output: {out}"

    async def test_fix_logic_error(self, tmp_path):
        """Fix a comparison operator bug."""
        scaffold_buggy_project(tmp_path, "sort_logic")
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "The sorting function produces wrong results. "
            "Run python3 test_sorter.py to see which tests fail, "
            "then fix the bug in sorter.py."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 test_sorter.py")
        assert ok, f"Tests still failing: {out}"
        assert "FAIL" not in out, f"Still has failures: {out}"

    async def test_fix_key_error(self, tmp_path):
        """Fix a KeyError in dictionary access."""
        scaffold_project(tmp_path, {
            "config.py": '''\
                import json

                DEFAULT_CONFIG = {
                    "host": "localhost",
                    "port": 8080,
                    "debug": False,
                    "log_level": "INFO",
                }

                def load_config(path="config.json"):
                    try:
                        with open(path) as f:
                            user_config = json.load(f)
                    except FileNotFoundError:
                        user_config = {}

                    merged = DEFAULT_CONFIG.copy()
                    merged.update(user_config)
                    return merged

                def get_database_url(config):
                    """Build database URL from config."""
                    # BUG: assumes 'database' key exists but it might not
                    db = config["database"]
                    return f"postgresql://{db['user']}:{db['password']}@{db['host']}/{db['name']}"

                if __name__ == "__main__":
                    config = load_config()
                    print(f"Server: {config['host']}:{config['port']}")
                    print(f"Database: {get_database_url(config)}")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "python3 config.py crashes with a KeyError. "
            "Fix it so it handles missing database config gracefully "
            "(use a default SQLite URL if database config is missing). "
            "Verify it runs without errors."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 config.py")
        assert ok, f"Still crashes: {out}"

    async def test_fix_index_error(self, tmp_path):
        """Fix an IndexError in list processing."""
        scaffold_project(tmp_path, {
            "processor.py": '''\
                def process_pairs(items):
                    """Process items in pairs, return list of (first, second) tuples."""
                    pairs = []
                    # BUG: range goes one too far when len is odd
                    for i in range(0, len(items), 2):
                        pairs.append((items[i], items[i + 1]))
                    return pairs

                def find_max_pair_sum(items):
                    """Find the pair with the highest sum."""
                    pairs = process_pairs(items)
                    return max(pairs, key=lambda p: p[0] + p[1])

                if __name__ == "__main__":
                    # Works with even count
                    print(process_pairs([1, 2, 3, 4]))
                    # Crashes with odd count
                    print(process_pairs([1, 2, 3, 4, 5]))
            ''',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "python3 processor.py crashes with IndexError when given an odd "
            "number of items. Fix process_pairs to handle odd-length lists "
            "(the last item should be paired with None). "
            "Verify it works with both even and odd lists."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 processor.py")
        assert ok, f"Still crashes: {out}"

    async def test_fix_type_error(self, tmp_path):
        """Fix a TypeError from string/int confusion."""
        scaffold_project(tmp_path, {
            "scores.py": '''\
                import sys

                def read_scores(filename):
                    """Read scores from file (one per line)."""
                    with open(filename) as f:
                        return [line.strip() for line in f if line.strip()]

                def calculate_stats(scores):
                    """Calculate mean, min, max."""
                    # BUG: scores are strings, not numbers
                    total = sum(scores)
                    return {
                        "mean": total / len(scores),
                        "min": min(scores),
                        "max": max(scores),
                        "count": len(scores),
                    }

                if __name__ == "__main__":
                    if len(sys.argv) < 2:
                        print("Usage: python3 scores.py <filename>")
                        sys.exit(1)
                    scores = read_scores(sys.argv[1])
                    stats = calculate_stats(scores)
                    for k, v in stats.items():
                        print(f"{k}: {v}")
            ''',
            "grades.txt": "85\n92\n78\n95\n88\n76\n91\n83\n",
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "python3 scores.py grades.txt crashes with a TypeError. "
            "Fix it and verify it prints correct statistics."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 scores.py grades.txt")
        assert ok, f"Still crashes: {out}"
        # Mean of [85,92,78,95,88,76,91,83] = 86.0
        assert "86" in out, f"Expected mean ~86 in output: {out}"

    async def test_fix_recursion_error(self, tmp_path):
        """Fix infinite recursion."""
        scaffold_project(tmp_path, {
            "tree.py": '''\
                class TreeNode:
                    def __init__(self, value, children=None):
                        self.value = value
                        self.children = children or []

                    def depth(self):
                        """Return the depth of the tree."""
                        if not self.children:
                            return 1
                        # BUG: calls self.depth() instead of child.depth()
                        return 1 + max(self.depth() for child in self.children)

                    def count(self):
                        """Count all nodes in the tree."""
                        return 1 + sum(child.count() for child in self.children)

                if __name__ == "__main__":
                    root = TreeNode("root", [
                        TreeNode("a", [TreeNode("a1"), TreeNode("a2")]),
                        TreeNode("b", [TreeNode("b1")]),
                    ])
                    print(f"Depth: {root.depth()}")
                    print(f"Count: {root.count()}")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "python3 tree.py crashes with RecursionError. "
            "Find the infinite recursion bug and fix it. "
            "Expected output: Depth: 3, Count: 6"
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 tree.py")
        assert ok, f"Still crashes: {out}"

    async def test_fix_file_not_closed(self, tmp_path):
        """Fix a resource leak (file not closed)."""
        scaffold_project(tmp_path, {
            "merger.py": '''\
                import sys

                def merge_files(output_path, *input_paths):
                    """Merge multiple text files into one."""
                    out = open(output_path, "w")
                    for path in input_paths:
                        f = open(path)
                        # BUG: doesn't close files, and crashes if file doesn't exist
                        content = f.read()
                        out.write(content)
                        out.write("\\n---\\n")
                    out.close()
                    return len(input_paths)

                if __name__ == "__main__":
                    if len(sys.argv) < 3:
                        print("Usage: merger.py output.txt input1.txt input2.txt ...")
                        sys.exit(1)
                    count = merge_files(sys.argv[1], *sys.argv[2:])
                    print(f"Merged {count} files")
            ''',
            "file1.txt": "Content of file 1\nLine 2\n",
            "file2.txt": "Content of file 2\nAnother line\n",
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "merger.py has resource leaks (files not properly closed) and "
            "crashes if an input file doesn't exist. Fix both issues using "
            "context managers (with statements). Also handle the missing file "
            "case by printing a warning and continuing. "
            "Test: python3 merger.py output.txt file1.txt file2.txt missing.txt"
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 merger.py output.txt file1.txt file2.txt")
        assert ok, f"Still broken: {out}"


# ============================================================================
# MEDIUM: Subtle bugs requiring investigation (5-15 min each)
# ============================================================================

class TestDebugMedium:
    """Bugs that aren't obvious from the error message."""

    async def test_fix_off_by_one(self, tmp_path):
        """Fix off-by-one pagination bug (classic interview question)."""
        scaffold_buggy_project(tmp_path, "off_by_one_pagination")
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "python3 test_paginator.py shows test failures. "
            "The pagination is returning wrong items. "
            "Find and fix the bug in paginator.py. "
            "All tests should pass."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 test_paginator.py")
        assert ok, f"Tests still failing: {out}"
        assert "FAIL" not in out, f"Still has failures: {out}"

    async def test_fix_data_pipeline(self, tmp_path):
        """Fix type coercion bug in data pipeline."""
        scaffold_buggy_project(tmp_path, "data_pipeline_crash")
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "Run: python3 -m pipeline.cli sample_data.csv --group-by department --sum-col salary\n"
            "It crashes. Find and fix the bug. The output should show salary "
            "totals per department."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path,
            "python3 -m pipeline.cli sample_data.csv --group-by department --sum-col salary")
        assert ok, f"Still crashes: {out}"

    async def test_fix_state_machine(self, tmp_path):
        """Fix state machine history recording."""
        scaffold_buggy_project(tmp_path, "state_machine_bug")
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "python3 test_statemachine.py has a failing test (test_history). "
            "The state machine's history should include the event name but it doesn't. "
            "Fix statemachine.py so history entries are (from_state, event, to_state)."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 test_statemachine.py")
        assert ok, f"Tests still failing: {out}"
        assert "FAIL" not in out, f"Still has failures: {out}"

    async def test_fix_router_params(self, tmp_path):
        """Fix URL router not passing path parameters to handlers."""
        scaffold_buggy_project(tmp_path, "flask_like_router")
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "python3 test_app.py has failures. The router matches URLs correctly "
            "but doesn't pass path parameters (like user_id from /users/<user_id>) "
            "to the handler function. Find and fix the bug."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 test_app.py")
        assert ok, f"Tests still failing: {out}"
        assert "FAIL" not in out, f"Still has failures: {out}"

    async def test_fix_race_condition(self, tmp_path):
        """Fix a threading bug in a counter."""
        scaffold_project(tmp_path, {
            "counter.py": '''\
                import threading
                import time

                class ThreadSafeCounter:
                    def __init__(self):
                        self.value = 0
                        # BUG: lock exists but increment doesn't use it
                        self._lock = threading.Lock()

                    def increment(self):
                        current = self.value
                        time.sleep(0.001)  # Simulate some work
                        self.value = current + 1

                    def get(self):
                        return self.value

                def worker(counter, n):
                    for _ in range(n):
                        counter.increment()

                if __name__ == "__main__":
                    counter = ThreadSafeCounter()
                    threads = []
                    n_threads = 4
                    increments_per_thread = 250

                    for _ in range(n_threads):
                        t = threading.Thread(target=worker, args=(counter, increments_per_thread))
                        threads.append(t)
                        t.start()

                    for t in threads:
                        t.join()

                    expected = n_threads * increments_per_thread
                    actual = counter.get()
                    print(f"Expected: {expected}, Got: {actual}")
                    if actual == expected:
                        print("PASS: Thread-safe!")
                    else:
                        print(f"FAIL: Lost {expected - actual} increments (race condition)")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "python3 counter.py shows a race condition — the final count is wrong. "
            "The ThreadSafeCounter has a lock but increment() doesn't use it. "
            "Fix the race condition. Verify by running it."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 counter.py")
        assert ok, f"Still broken: {out}"
        assert "PASS" in out, f"Race condition not fixed: {out}"

    async def test_fix_encoding_bug(self, tmp_path):
        """Fix a Unicode handling bug."""
        scaffold_project(tmp_path, {
            "textproc.py": '''\
                def word_count(text):
                    """Count words in text."""
                    return len(text.split())

                def char_frequency(text):
                    """Return character frequency dict."""
                    freq = {}
                    for ch in text.lower():
                        if ch.isalpha():
                            freq[ch] = freq.get(ch, 0) + 1
                    return freq

                def truncate(text, max_length):
                    """Truncate text to max_length, adding ... if truncated."""
                    # BUG: doesn't handle multi-byte characters correctly
                    if len(text.encode('utf-8')) <= max_length:
                        return text
                    # Truncating bytes then decoding can split multi-byte chars
                    return text.encode('utf-8')[:max_length - 3].decode('utf-8') + '...'

                if __name__ == "__main__":
                    # ASCII works fine
                    print(truncate("Hello World", 8))
                    # Multi-byte chars break
                    print(truncate("Héllo Wörld café", 12))
                    print(truncate("日本語テスト", 10))
            ''',
        })
        agent = make_agent(tmp_path, max_turns=15)
        r = await run_workload(agent,
            "python3 textproc.py crashes on Unicode text. The truncate function "
            "truncates bytes instead of characters, which splits multi-byte UTF-8. "
            "Fix it to truncate by character count, not byte count. "
            "Verify all three test cases work."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 textproc.py")
        assert ok, f"Still crashes: {out}"

    async def test_fix_circular_import(self, tmp_path):
        """Fix circular import between modules."""
        scaffold_project(tmp_path, {
            "app/__init__.py": "",
            "app/models.py": '''\
                from app.validators import validate_email

                class User:
                    def __init__(self, name, email):
                        validate_email(email)
                        self.name = name
                        self.email = email

                    def greet(self):
                        return f"Hello, {self.name}!"
            ''',
            "app/validators.py": '''\
                # BUG: circular import — validators imports models, models imports validators
                from app.models import User

                def validate_email(email):
                    if not isinstance(email, str) or "@" not in email:
                        raise ValueError(f"Invalid email: {email}")
                    return True

                def validate_user(user):
                    """Validate a User object."""
                    if not isinstance(user, User):
                        raise TypeError("Expected User instance")
                    validate_email(user.email)
                    return True
            ''',
            "main.py": '''\
                from app.models import User

                u = User("Alice", "alice@example.com")
                print(u.greet())
            ''',
        })
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "python3 main.py crashes with ImportError (circular import). "
            "Fix the circular dependency between app/models.py and app/validators.py. "
            "Both modules need each other's functionality. "
            "Verify main.py runs correctly."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 main.py")
        assert ok, f"Still crashes: {out}"
        assert "Hello" in out, f"Expected greeting in output: {out}"


# ============================================================================
# HARD: Complex multi-file debugging (10-30 min each)
# ============================================================================

class TestDebugHard:
    """Complex bugs spanning multiple files or requiring architectural changes."""

    async def test_fix_pipeline_crash(self, tmp_path):
        """Fix multiple connected bugs in a data pipeline."""
        scaffold_project(tmp_path, {
            "pipeline/__init__.py": "",
            "pipeline/extract.py": '''\
                import json
                import csv

                def extract_json(path):
                    with open(path) as f:
                        data = json.load(f)
                    # Normalize: ensure all records have same keys
                    if data:
                        all_keys = set()
                        for record in data:
                            all_keys.update(record.keys())
                        for record in data:
                            for key in all_keys:
                                record.setdefault(key, None)
                    return data

                def extract_csv(path):
                    with open(path) as f:
                        reader = csv.DictReader(f)
                        return list(reader)
            ''',
            "pipeline/transform.py": '''\
                from datetime import datetime

                def parse_dates(records, date_field, fmt="%Y-%m-%d"):
                    """Parse string dates into datetime objects."""
                    for r in records:
                        if r.get(date_field):
                            # BUG 1: modifies in place AND returns, inconsistent
                            r[date_field] = datetime.strptime(r[date_field], fmt)
                    return records

                def filter_by_date(records, date_field, after=None, before=None):
                    """Filter records by date range."""
                    result = []
                    for r in records:
                        d = r.get(date_field)
                        # BUG 2: comparing datetime with string if parse_dates wasn't called
                        if after and d < after:
                            continue
                        if before and d > before:
                            continue
                        result.append(r)
                    return result

                def aggregate_by(records, group_field, value_field, agg="sum"):
                    """Group records and aggregate a numeric field."""
                    groups = {}
                    for r in records:
                        key = r.get(group_field, "Unknown")
                        # BUG 3: CSV values are strings, need float conversion
                        val = r.get(value_field, 0)
                        groups.setdefault(key, []).append(val)

                    result = {}
                    for key, values in groups.items():
                        if agg == "sum":
                            result[key] = sum(values)
                        elif agg == "avg":
                            result[key] = sum(values) / len(values)
                        elif agg == "count":
                            result[key] = len(values)
                    return result
            ''',
            "pipeline/load.py": '''\
                import json
                import csv
                from datetime import datetime

                def _serialize(obj):
                    if isinstance(obj, datetime):
                        return obj.isoformat()
                    raise TypeError(f"Cannot serialize {type(obj)}")

                def to_json(data, path):
                    with open(path, "w") as f:
                        json.dump(data, f, indent=2, default=_serialize)

                def to_csv(records, path):
                    if not records:
                        return
                    with open(path, "w", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=records[0].keys())
                        writer.writeheader()
                        writer.writerows(records)
            ''',
            "pipeline/run.py": '''\
                import sys
                from pipeline.extract import extract_csv
                from pipeline.transform import parse_dates, aggregate_by
                from pipeline.load import to_json

                def main():
                    if len(sys.argv) < 2:
                        print("Usage: python3 -m pipeline.run <input.csv>")
                        sys.exit(1)

                    records = extract_csv(sys.argv[1])
                    records = parse_dates(records, "date")
                    summary = aggregate_by(records, "category", "amount")
                    print("Summary by category:")
                    for k, v in sorted(summary.items()):
                        print(f"  {k}: ${v:,.2f}")
                    to_json(summary, "summary.json")
                    print("Saved to summary.json")

                if __name__ == "__main__":
                    main()
            ''',
            "pipeline/__main__.py": '''\
                from pipeline.run import main
                main()
            ''',
            "sales.csv": (
                "date,category,amount,region\n"
                "2024-01-15,Electronics,299.99,East\n"
                "2024-01-16,Clothing,49.95,West\n"
                "2024-01-16,Electronics,599.00,East\n"
                "2024-01-17,Food,23.50,East\n"
                "2024-01-17,Clothing,89.99,West\n"
                "2024-01-18,Electronics,149.99,West\n"
                "2024-01-18,Food,45.00,East\n"
            ),
        })
        agent = make_agent(tmp_path, max_turns=30)
        r = await run_workload(agent,
            "Run: python3 -m pipeline.run sales.csv\n"
            "It crashes with TypeError in pipeline/transform.py. "
            "The bug: CSV data values are strings (like '299.99'), but aggregate_by() "
            "tries to sum them directly. You need to convert to float before summing. "
            "Fix pipeline/transform.py and verify the pipeline runs."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 -m pipeline.run sales.csv")
        assert ok, f"Pipeline still crashes: {out}"
        assert "summary.json" in out.lower() or (tmp_path / "summary.json").exists()

    async def test_fix_web_framework(self, tmp_path):
        """Fix router parameter passing bug in a web framework."""
        scaffold_buggy_project(tmp_path, "flask_like_router")
        agent = make_agent(tmp_path, max_turns=30)
        r = await run_workload(agent,
            "This is a mini web framework. Run python3 test_app.py to see failures. "
            "Multiple tests fail. Investigate the framework code in framework/ "
            "and fix all bugs. All 4 tests must pass."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 test_app.py")
        assert ok, f"Tests still failing: {out}"
        assert out.count("PASS") >= 3, f"Not enough tests passing: {out}"

    async def test_fix_event_driven_system(self, tmp_path):
        """Fix bugs in an event-driven notification system."""
        scaffold_project(tmp_path, {
            "notifications/__init__.py": "",
            "notifications/events.py": '''\
                from dataclasses import dataclass, field
                from typing import Any
                from datetime import datetime

                @dataclass
                class Event:
                    type: str
                    data: dict = field(default_factory=dict)
                    timestamp: datetime = field(default_factory=datetime.now)
                    source: str = ""
            ''',
            "notifications/handlers.py": '''\
                from typing import Callable

                class HandlerRegistry:
                    def __init__(self):
                        self._handlers: dict[str, list[Callable]] = {}
                        self._once: set[int] = set()  # IDs of one-time handlers

                    def on(self, event_type: str, handler: Callable):
                        self._handlers.setdefault(event_type, []).append(handler)

                    def once(self, event_type: str, handler: Callable):
                        self._handlers.setdefault(event_type, []).append(handler)
                        self._once.add(id(handler))

                    def emit(self, event):
                        handlers = self._handlers.get(event.type, [])
                        to_remove = []
                        for handler in handlers:
                            handler(event)
                            if id(handler) in self._once:
                                to_remove.append(handler)
                                # BUG: removes from set during iteration conceptually,
                                # but worse: modifying list while iterating
                        for h in to_remove:
                            handlers.remove(h)
                            self._once.discard(id(h))

                    def off(self, event_type: str, handler: Callable):
                        if event_type in self._handlers:
                            # BUG: doesn't handle case where handler isn't registered
                            self._handlers[event_type].remove(handler)
            ''',
            "notifications/dispatcher.py": '''\
                from notifications.events import Event
                from notifications.handlers import HandlerRegistry

                class NotificationDispatcher:
                    def __init__(self):
                        self.registry = HandlerRegistry()
                        self._history = []
                        self._max_history = 100

                    def subscribe(self, event_type, handler, once=False):
                        if once:
                            self.registry.once(event_type, handler)
                        else:
                            self.registry.on(event_type, handler)

                    def unsubscribe(self, event_type, handler):
                        self.registry.off(event_type, handler)

                    def dispatch(self, event_type, **data):
                        event = Event(type=event_type, data=data)
                        self._history.append(event)
                        if len(self._history) > self._max_history:
                            self._history = self._history[-self._max_history:]
                        self.registry.emit(event)

                    @property
                    def history(self):
                        return list(self._history)
            ''',
            "test_notifications.py": '''\
                from notifications.dispatcher import NotificationDispatcher

                def test_basic_subscribe():
                    d = NotificationDispatcher()
                    received = []
                    d.subscribe("user.created", lambda e: received.append(e.data))
                    d.dispatch("user.created", name="Alice")
                    d.dispatch("user.created", name="Bob")
                    assert len(received) == 2, f"Expected 2, got {len(received)}"
                    assert received[0]["name"] == "Alice"

                def test_once():
                    d = NotificationDispatcher()
                    received = []
                    d.subscribe("alert", lambda e: received.append(1), once=True)
                    d.dispatch("alert")
                    d.dispatch("alert")
                    d.dispatch("alert")
                    assert len(received) == 1, f"Once handler fired {len(received)} times"

                def test_unsubscribe():
                    d = NotificationDispatcher()
                    received = []
                    handler = lambda e: received.append(1)
                    d.subscribe("event", handler)
                    d.dispatch("event")
                    d.unsubscribe("event", handler)
                    d.dispatch("event")
                    assert len(received) == 1

                def test_unsubscribe_nonexistent():
                    """Unsubscribing a handler that was never registered should not crash."""
                    d = NotificationDispatcher()
                    handler = lambda e: None
                    try:
                        d.unsubscribe("event", handler)
                        print("PASS: test_unsubscribe_nonexistent")
                    except ValueError:
                        print("FAIL: test_unsubscribe_nonexistent: raised ValueError")
                        raise AssertionError("Should not crash")

                def test_history():
                    d = NotificationDispatcher()
                    for i in range(5):
                        d.dispatch("tick", count=i)
                    assert len(d.history) == 5

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
            "python3 test_notifications.py shows failures. The notification system "
            "has bugs in handlers.py. Fix all bugs: "
            "1. once() handlers should only fire once "
            "2. off() should not crash when removing non-existent handler "
            "All tests must pass."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 test_notifications.py")
        assert ok, f"Tests still failing: {out}"
        assert "FAIL" not in out, f"Still has failures: {out}"

    async def test_fix_config_system(self, tmp_path):
        """Fix a configuration system with inheritance and validation."""
        scaffold_project(tmp_path, {
            "config/__init__.py": "",
            "config/schema.py": '''\
                SCHEMA = {
                    "server": {
                        "host": {"type": str, "default": "0.0.0.0"},
                        "port": {"type": int, "default": 8080, "min": 1, "max": 65535},
                        "workers": {"type": int, "default": 4, "min": 1},
                    },
                    "database": {
                        "url": {"type": str, "required": True},
                        "pool_size": {"type": int, "default": 5, "min": 1, "max": 100},
                        "echo": {"type": bool, "default": False},
                    },
                    "logging": {
                        "level": {"type": str, "default": "INFO",
                                  "choices": ["DEBUG", "INFO", "WARNING", "ERROR"]},
                        "file": {"type": str, "default": None},
                    },
                }
            ''',
            "config/loader.py": '''\
                import json
                import os
                from config.schema import SCHEMA

                class ConfigError(Exception):
                    pass

                def _validate_field(key, value, spec):
                    """Validate a single field against its spec."""
                    if spec.get("type") and not isinstance(value, spec["type"]):
                        # BUG 1: doesn't try to coerce types (e.g., "8080" -> 8080)
                        raise ConfigError(f"{key}: expected {spec['type'].__name__}, got {type(value).__name__}")
                    if "min" in spec and value < spec["min"]:
                        raise ConfigError(f"{key}: {value} < minimum {spec['min']}")
                    if "max" in spec and value > spec["max"]:
                        raise ConfigError(f"{key}: {value} > maximum {spec['max']}")
                    if "choices" in spec and value not in spec["choices"]:
                        raise ConfigError(f"{key}: {value} not in {spec['choices']}")

                def load_config(path=None, env_prefix="APP_"):
                    """Load config from file + environment variables."""
                    config = {}

                    # Load from file
                    if path and os.path.exists(path):
                        with open(path) as f:
                            config = json.load(f)

                    # Apply defaults and validate
                    result = {}
                    for section, fields in SCHEMA.items():
                        result[section] = {}
                        section_conf = config.get(section, {})
                        for field, spec in fields.items():
                            # Check env var
                            env_key = f"{env_prefix}{section.upper()}_{field.upper()}"
                            env_val = os.environ.get(env_key)

                            if env_val is not None:
                                value = env_val  # BUG 2: env vars are always strings
                            elif field in section_conf:
                                value = section_conf[field]
                            elif "default" in spec:
                                value = spec["default"]
                            elif spec.get("required"):
                                raise ConfigError(f"{section}.{field} is required")
                            else:
                                continue

                            if value is not None:
                                _validate_field(f"{section}.{field}", value, spec)
                            result[section][field] = value

                    return result
            ''',
            "config/printer.py": '''\
                def print_config(config, indent=0):
                    """Pretty-print a config dict."""
                    for key, value in config.items():
                        if isinstance(value, dict):
                            print(f"{'  ' * indent}{key}:")
                            print_config(value, indent + 1)
                        else:
                            print(f"{'  ' * indent}{key}: {value}")
            ''',
            "app_config.json": '''\
{
    "server": {"host": "localhost", "port": 3000},
    "database": {"url": "sqlite:///app.db"},
    "logging": {"level": "DEBUG"}
}
''',
            "test_config.py": '''\
                import os
                import sys
                sys.path.insert(0, ".")
                from config.loader import load_config, ConfigError

                def test_load_from_file():
                    config = load_config("app_config.json")
                    assert config["server"]["host"] == "localhost"
                    assert config["server"]["port"] == 3000
                    assert config["database"]["url"] == "sqlite:///app.db"

                def test_defaults():
                    config = load_config("app_config.json")
                    assert config["server"]["workers"] == 4  # default
                    assert config["database"]["pool_size"] == 5  # default

                def test_env_override():
                    os.environ["APP_SERVER_PORT"] = "9090"
                    try:
                        config = load_config("app_config.json")
                        assert config["server"]["port"] == 9090, \\
                            f"Expected 9090, got {config['server']['port']} (type: {type(config['server']['port'])})"
                    finally:
                        del os.environ["APP_SERVER_PORT"]

                def test_missing_required():
                    try:
                        load_config()  # No file, no env — database.url is required
                        assert False, "Should have raised ConfigError"
                    except ConfigError as e:
                        assert "required" in str(e).lower()

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
            "python3 test_config.py has failures. The config loader has two bugs:\n"
            "1. It doesn't coerce types (e.g., string '3000' from JSON should work as int)\n"
            "2. Environment variables are strings but validation expects typed values\n"
            "Fix config/loader.py so all tests pass."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        ok, out = check_runs(tmp_path, "python3 test_config.py")
        assert ok, f"Tests still failing: {out}"

    async def test_debug_from_traceback(self, tmp_path):
        """Give DryDock only a traceback and ask it to find/fix the bug."""
        scaffold_project(tmp_path, {
            "inventory/__init__.py": "",
            "inventory/models.py": '''\
                from dataclasses import dataclass, field
                from datetime import datetime

                @dataclass
                class Product:
                    name: str
                    sku: str
                    price: float
                    quantity: int = 0
                    category: str = ""
                    created_at: datetime = field(default_factory=datetime.now)

                    @property
                    def value(self):
                        return self.price * self.quantity

                    def __str__(self):
                        return f"{self.name} ({self.sku}): ${self.price:.2f} x {self.quantity}"
            ''',
            "inventory/store.py": '''\
                import json
                from pathlib import Path
                from inventory.models import Product

                class InventoryStore:
                    def __init__(self, path="inventory.json"):
                        self.path = Path(path)
                        self.products: dict[str, Product] = {}
                        self._load()

                    def _load(self):
                        if self.path.exists():
                            with open(self.path) as f:
                                data = json.load(f)
                            for item in data:
                                # BUG: Product doesn't accept created_at as string
                                p = Product(**item)
                                self.products[p.sku] = p

                    def _save(self):
                        data = []
                        for p in self.products.values():
                            d = {
                                "name": p.name, "sku": p.sku,
                                "price": p.price, "quantity": p.quantity,
                                "category": p.category,
                                "created_at": p.created_at.isoformat(),
                            }
                            data.append(d)
                        with open(self.path, "w") as f:
                            json.dump(data, f, indent=2)

                    def add(self, name, sku, price, quantity=0, category=""):
                        p = Product(name=name, sku=sku, price=price,
                                    quantity=quantity, category=category)
                        self.products[sku] = p
                        self._save()
                        return p

                    def get(self, sku):
                        return self.products.get(sku)

                    def update_quantity(self, sku, delta):
                        p = self.products.get(sku)
                        if not p:
                            raise KeyError(f"Product not found: {sku}")
                        p.quantity += delta
                        if p.quantity < 0:
                            p.quantity = 0
                        self._save()

                    def total_value(self):
                        return sum(p.value for p in self.products.values())

                    def low_stock(self, threshold=5):
                        return [p for p in self.products.values() if p.quantity <= threshold]
            ''',
            "main.py": '''\
                from inventory.store import InventoryStore

                store = InventoryStore("test_inventory.json")

                # Add some products
                store.add("Widget", "WGT-001", 9.99, 100, "Parts")
                store.add("Gadget", "GDG-002", 24.99, 3, "Electronics")
                store.add("Doohickey", "DHK-003", 4.99, 50, "Parts")

                print(f"Total inventory value: ${store.total_value():,.2f}")
                print(f"Low stock items: {[str(p) for p in store.low_stock()]}")

                # This works first time, but crashes on second run
                # because _load() tries to reconstruct Product from JSON
                # and created_at is now a string, not a datetime
                print("\\nReloading store...")
                store2 = InventoryStore("test_inventory.json")
                print(f"Reloaded {len(store2.products)} products")
            ''',
        })
        agent = make_agent(tmp_path, max_turns=25)
        r = await run_workload(agent,
            "I got this error on the second run of main.py:\n\n"
            "Traceback (most recent call last):\n"
            "  File \"main.py\", line 15, in <module>\n"
            "    store2 = InventoryStore(\"test_inventory.json\")\n"
            "  File \"inventory/store.py\", line 14, in __init__\n"
            "    self._load()\n"
            "  File \"inventory/store.py\", line 20, in _load\n"
            "    p = Product(**item)\n"
            "TypeError: __init__() got an unexpected keyword argument\n\n"
            "Find and fix the bug. It works on first run but crashes when "
            "loading from the saved JSON file."
        )

        assert r.ok, f"Ordering crash: {r.summary()}"
        # Run twice — should work both times
        ok1, out1 = check_runs(tmp_path, "python3 main.py")
        assert ok1, f"First run failed: {out1}"
        ok2, out2 = check_runs(tmp_path, "python3 main.py")
        assert ok2, f"Second run failed (the actual bug): {out2}"

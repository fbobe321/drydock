"""Tests for the prolog built-in tool (pytholog-backed)."""
from __future__ import annotations

import asyncio

import pytest

from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.prolog_tool import (
    Prolog,
    PrologArgs,
    PrologConfig,
    PrologResult,
    _build_kb,
    _validate_kb,
)


def _drive(args: PrologArgs) -> PrologResult:
    tool = Prolog(config=PrologConfig(), state=BaseToolState())

    async def go() -> PrologResult:
        result = None
        async for ev in tool.run(args):
            if isinstance(ev, PrologResult):
                result = ev
        assert result is not None
        return result

    return asyncio.run(go())


# ── query ───────────────────────────────────────────────────────────────

def test_query_family_relations():
    r = _drive(PrologArgs(
        op="query",
        facts=["parent(tom, bob)", "parent(bob, ann)", "parent(bob, pat)"],
        rules=["grandparent(X, Y) :- parent(X, Z), parent(Z, Y)"],
        query="grandparent(tom, X)",
    ))
    assert r.ok
    xs = {b["X"] for b in r.bindings}
    assert xs == {"ann", "pat"}


def test_query_ground_yes():
    r = _drive(PrologArgs(
        op="query",
        facts=["likes(alice, pizza)"],
        query="likes(alice, pizza)",
    ))
    assert r.ok
    assert r.summary == "Yes"
    assert r.bindings == []


def test_query_ground_no():
    r = _drive(PrologArgs(
        op="query",
        facts=["likes(alice, pizza)"],
        query="likes(alice, salad)",
    ))
    assert r.ok
    assert r.summary == "No"
    assert r.bindings == []


def test_query_recursive_ancestor():
    r = _drive(PrologArgs(
        op="query",
        facts=["parent(a, b)", "parent(b, c)", "parent(c, d)"],
        rules=[
            "ancestor(X, Y) :- parent(X, Y)",
            "ancestor(X, Y) :- parent(X, Z), ancestor(Z, Y)",
        ],
        query="ancestor(a, X)",
    ))
    assert r.ok
    xs = {b["X"] for b in r.bindings}
    assert xs == {"b", "c", "d"}


def test_query_summary_counts_solutions():
    r = _drive(PrologArgs(
        op="query",
        facts=["color(red)", "color(green)", "color(blue)"],
        query="color(X)",
    ))
    assert r.ok
    assert r.summary == "3 solution(s)"
    assert len(r.bindings) == 3


# ── consult ─────────────────────────────────────────────────────────────

def test_consult_validates_kb_compiles():
    r = _drive(PrologArgs(
        op="consult",
        facts=["foo(1)", "foo(2)", "bar(baz)"],
        rules=["q(X) :- foo(X), bar(_)"],
    ))
    assert r.ok
    assert r.summary == "ok"
    assert r.kb_size == 4  # 3 facts + 1 rule


# ── assert_and_query ────────────────────────────────────────────────────

def test_assert_and_query_returns_kb_size():
    r = _drive(PrologArgs(
        op="assert_and_query",
        facts=["a(1)", "a(2)", "a(3)"],
        query="a(X)",
    ))
    assert r.ok
    assert r.op == "assert_and_query"
    assert r.kb_size == 3
    assert len(r.bindings) == 3


# ── Validation ──────────────────────────────────────────────────────────

def test_query_rejects_empty_kb():
    r = _drive(PrologArgs(op="query", facts=[], rules=[], query="foo(X)"))
    assert not r.ok
    assert "empty" in r.error.lower()


def test_query_requires_query_string():
    r = _drive(PrologArgs(op="query", facts=["foo(1)"], query=""))
    assert not r.ok
    assert "empty" in r.error.lower()


@pytest.mark.parametrize("bad_token", [
    "__import__(os)",
    "exec('print(1)')",
    "open('/etc/passwd')",
    "eval('1+1')",
])
def test_forbidden_tokens_rejected(bad_token: str):
    r = _drive(PrologArgs(
        op="query", facts=[bad_token], rules=[], query="foo(X)",
    ))
    assert not r.ok
    assert "forbidden" in r.error.lower()


def test_fact_count_cap():
    facts = [f"foo({i})" for i in range(250)]  # > 200
    r = _drive(PrologArgs(op="query", facts=facts, query="foo(X)"))
    assert not r.ok
    assert "too many facts" in r.error


def test_rule_count_cap():
    rules = [f"r{i}(X) :- foo(X)" for i in range(60)]  # > 50
    r = _drive(PrologArgs(
        op="query", facts=["foo(1)"], rules=rules, query="foo(X)",
    ))
    assert not r.ok
    assert "too many rules" in r.error


def test_overlong_entry_rejected():
    long_fact = "foo(" + "a" * 2000 + ")"
    r = _drive(PrologArgs(op="query", facts=[long_fact], query="foo(X)"))
    assert not r.ok
    assert "too long" in r.error


# ── Discovery ──────────────────────────────────────────────────────────

def test_tool_is_discoverable():
    """prolog should be auto-registered by ToolManager scanning builtins."""
    from drydock.core.paths import DEFAULT_TOOL_DIR
    from drydock.core.tools.manager import ToolManager
    classes = list(ToolManager._iter_tool_classes([DEFAULT_TOOL_DIR.path]))
    names = {c.get_name() for c in classes}
    assert "prolog" in names


# ── End-to-end: a small logic puzzle ────────────────────────────────────

def test_small_logic_puzzle_employee_department():
    """3 employees, 3 departments; constraints determine the assignment."""
    r = _drive(PrologArgs(
        op="query",
        facts=[
            "person(alice)", "person(bob)", "person(carol)",
            "dept(engineering)", "dept(sales)", "dept(hr)",
            "works_in(alice, engineering)",
            "works_in(bob, sales)",
            "works_in(carol, hr)",
        ],
        rules=[
            "colleagues(X, Y) :- works_in(X, D), works_in(Y, D), X \\= Y",
        ],
        query="works_in(carol, X)",
    ))
    assert r.ok
    # carol → hr
    assert len(r.bindings) == 1
    assert r.bindings[0]["X"] == "hr"

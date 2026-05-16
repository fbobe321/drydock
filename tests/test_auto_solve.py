"""Tests for the auto-solve synthetic-tool-call hook."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from drydock.core.auto_solve import (
    _build_args,
    _format_result,
    _run_solve_sync,
    maybe_inject_auto_solve,
)
from drydock.core.constraint_extract import ExtractResult, extract


# ── _build_args ─────────────────────────────────────────────────────────

def test_build_args_perfect_square():
    extr = ExtractResult(
        predicate="perfect_square",
        formula="x * x * x - 1",
        variables=["x"],
    )
    args = _build_args(extr)
    assert args is not None
    assert args["op"] == "find_all"
    assert args["variables"] == "x:Int, y:Int"
    assert "y * y == x * x * x - 1" in args["constraints"][0]
    assert "y >= 0" in args["constraints"]


def test_build_args_divisible_by():
    extr = ExtractResult(
        predicate="divisible_by",
        formula="x * x + 1",
        variables=["x"],
        divisor=5,
    )
    args = _build_args(extr)
    assert args is not None
    assert "(x * x + 1) % 5 == 0" in args["constraints"][0]


def test_build_args_equation_solve():
    extr = ExtractResult(
        predicate="equation_solve",
        formula="x * x - 5 * x + 6",
        second_formula="0",
        variables=["x"],
    )
    args = _build_args(extr)
    assert args is not None
    assert "(x * x - 5 * x + 6) == (0)" in args["constraints"][0]


def test_build_args_smallest_with_uses_optimize():
    extr = ExtractResult(
        predicate="smallest_with",
        formula="n * n > 1000",
        variables=["n"],
    )
    args = _build_args(extr)
    assert args is not None
    assert args["op"] == "optimize"
    assert args["direction"] == "min"
    assert args["objective"] == "n"


def test_build_args_largest_with_uses_max():
    extr = ExtractResult(
        predicate="largest_with",
        formula="x * x < 50",
        variables=["x"],
    )
    args = _build_args(extr)
    assert args is not None
    assert args["op"] == "optimize"
    assert args["direction"] == "max"


def test_build_args_returns_none_for_prime():
    """Z3 can't decide primality — caller should fall through to the
    advisory template, which routes prime checks via number_theory."""
    extr = ExtractResult(
        predicate="prime",
        formula="n * n + n + 41",
        variables=["n"],
    )
    assert _build_args(extr) is None


def test_build_args_returns_none_for_empty_variables():
    extr = ExtractResult(
        predicate="perfect_square",
        formula="0",
        variables=[],
    )
    assert _build_args(extr) is None


# ── _run_solve_sync (real Z3) ───────────────────────────────────────────

def test_run_solve_sync_perfect_square_hle_classic():
    """The HLE x^3 - 16x^2 - 72x + 1056 perfect-square question.
    Verifies the synthetic-tool-call path returns the correct count (4)
    when called end-to-end with no mocking."""
    args = {
        "op": "find_all",
        "variables": "x:Int, y:Int",
        "constraints": [
            "y * y == x*x*x - 16*x*x - 72*x + 1056",
            "x >= -200", "x <= 200", "y >= 0",
        ],
        "limit": 100,
        "timeout_ms": 30000,
    }
    result = _run_solve_sync(args)
    assert result is not None
    assert result["status"] == "sat"
    assert len(result["models"]) == 4
    xs = {int(m.split(",")[0].split("=")[1]) for m in result["models"]}
    assert xs == {-4, 4, 17, 65}


def test_run_solve_sync_returns_none_for_unsat():
    args = {
        "op": "find_all",
        "variables": "x:Int",
        "constraints": ["x > 0", "x < 0"],
        "limit": 5,
    }
    result = _run_solve_sync(args)
    # unsat is still ok-status, just empty models. _run_solve_sync
    # returns the dict; the caller decides what to do.
    assert result is not None
    assert result["status"] == "unsat"


# ── _format_result ──────────────────────────────────────────────────────

def test_format_result_emits_answer_line_for_counting():
    extr = ExtractResult(
        predicate="perfect_square",
        formula="x*x*x - 1",
        variables=["x"],
    )
    args = {"constraints": ["y*y == x*x*x - 1", "x >= -200", "x <= 200", "y >= 0"]}
    result = {"status": "sat", "models": ["x=1, y=0", "x=2, y=2"], "model": "", "objective_value": ""}
    out = _format_result(args, result, extr)
    assert "solutions found: 2" in out
    assert "ANSWER: 2" in out


def test_format_result_emits_answer_for_optimize():
    extr = ExtractResult(
        predicate="smallest_with",
        formula="n*n > 1000",
        variables=["n"],
    )
    args = {"constraints": []}
    result = {"status": "optimal", "model": "n=32", "objective_value": "32",
              "models": []}
    out = _format_result(args, result, extr)
    assert "ANSWER: 32" in out


# ── maybe_inject_auto_solve (orchestration) ─────────────────────────────

class _StubMessages:
    """Minimal stand-in for the agent_loop's `self.messages` —
    just records what was appended."""
    def __init__(self):
        self.items = []

    def append(self, item):
        self.items.append(item)


def test_maybe_inject_appends_pair_for_z3_solvable():
    msgs = _StubMessages()
    q = "For how many integers x is x^3 - 1 a perfect square?"
    with patch.dict(os.environ, {"DRYDOCK_AUTO_SOLVE": "1"}, clear=False):
        ok = maybe_inject_auto_solve(msgs, q)
    assert ok is True
    # Synthetic assistant + tool messages should both be appended.
    assert len(msgs.items) == 2
    # First: assistant with tool_calls
    assert msgs.items[0].role.value == "assistant" or str(msgs.items[0].role).endswith("assistant")
    assert msgs.items[0].tool_calls
    assert msgs.items[0].tool_calls[0].function.name == "solve"
    # Second: tool with the formatted result
    assert "ANSWER:" in msgs.items[1].content


def test_maybe_inject_skips_when_disabled():
    msgs = _StubMessages()
    q = "For how many integers x is x^3 - 1 a perfect square?"
    with patch.dict(os.environ, {"DRYDOCK_AUTO_SOLVE": "0"}, clear=False):
        ok = maybe_inject_auto_solve(msgs, q)
    assert ok is False
    assert msgs.items == []


def test_maybe_inject_skips_when_no_extract_match():
    msgs = _StubMessages()
    with patch.dict(os.environ, {"DRYDOCK_AUTO_SOLVE": "1"}, clear=False):
        ok = maybe_inject_auto_solve(msgs, "Build me a calculator.")
    assert ok is False
    assert msgs.items == []


def test_maybe_inject_skips_for_prime_predicate():
    """Prime checks require factoring — caller falls through to the
    advisory template which routes via number_theory.is_prime."""
    msgs = _StubMessages()
    q = "For how many positive integers n is n^2 + n + 41 prime?"
    with patch.dict(os.environ, {"DRYDOCK_AUTO_SOLVE": "1"}, clear=False):
        ok = maybe_inject_auto_solve(msgs, q)
    assert ok is False
    assert msgs.items == []


def test_maybe_inject_skips_for_short_message():
    msgs = _StubMessages()
    with patch.dict(os.environ, {"DRYDOCK_AUTO_SOLVE": "1"}, clear=False):
        ok = maybe_inject_auto_solve(msgs, "hi")
    assert ok is False


# ── _formula_is_z3_friendly pre-flight ─────────────────────────────────

@pytest.mark.parametrize("formula,expected", [
    ("x * x * x - 1", True),
    ("16 * x + 5", True),
    ("(x + 1) * (x - 1)", True),
    # Factorial — Z3 can't handle
    ("n! - 1", False),
    ("n * (n - 1)!", False),
    # Variable exponents
    ("2 ** n - 1", False),
    ("3 ** k + 2", False),
    # Transcendentals
    ("sin(x) + cos(x)", False),
    ("exp(x) - 1", False),
    ("log(x)", False),
    ("ln(x) + x", False),
    ("sqrt(x) + 1", False),
])
def test_formula_pre_flight(formula: str, expected: bool):
    from drydock.core.auto_solve import _formula_is_z3_friendly
    assert _formula_is_z3_friendly(formula) is expected


def test_maybe_inject_skips_factorial():
    """Pre-flight should reject n! before launching Z3."""
    msgs = _StubMessages()
    # Need a question that triggers the extractor with a bad formula.
    # "What is the smallest n with n! >= 100?" extracts to smallest_with
    # with formula "n! >= 100" — Z3 can't handle factorial.
    q = "What is the smallest n with n! >= 100?"
    with patch.dict(os.environ, {"DRYDOCK_AUTO_SOLVE": "1"}, clear=False):
        ok = maybe_inject_auto_solve(msgs, q)
    assert ok is False
    assert msgs.items == []

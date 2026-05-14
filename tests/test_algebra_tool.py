"""Tests for the algebra built-in — symbolic math via sympy."""
from __future__ import annotations

import asyncio

import pytest

from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.algebra_tool import (
    Algebra,
    AlgebraArgs,
    AlgebraConfig,
    AlgebraResult,
)


def _drive(args: AlgebraArgs) -> AlgebraResult:
    tool = Algebra(config=AlgebraConfig(), state=BaseToolState())

    async def go() -> AlgebraResult:
        result = None
        async for ev in tool.run(args):
            if isinstance(ev, AlgebraResult):
                result = ev
        assert result is not None
        return result

    return asyncio.run(go())


# ── Solve ──────────────────────────────────────────────────────────────

def test_solve_quadratic():
    r = _drive(AlgebraArgs(op="solve", expression="x**2 - 4", variable="x"))
    assert r.ok and r.result == "[-2, 2]"


def test_solve_accepts_Eq_form():
    r = _drive(AlgebraArgs(op="solve", expression="Eq(x**2, 4)", variable="x"))
    assert r.ok and r.result == "[-2, 2]"


def test_solve_different_variable():
    r = _drive(AlgebraArgs(op="solve", expression="y**2 - 9", variable="y"))
    assert r.ok and r.result == "[-3, 3]"


def test_solve_linear():
    r = _drive(AlgebraArgs(op="solve", expression="3*x + 6", variable="x"))
    assert r.ok and r.result == "[-2]"


# ── Simplify / Expand / Factor ─────────────────────────────────────────

def test_simplify_cancels_common_factor():
    r = _drive(AlgebraArgs(op="simplify", expression="(x**2 - 1)/(x - 1)"))
    assert r.ok and r.result == "x + 1"


def test_expand_cube():
    r = _drive(AlgebraArgs(op="expand", expression="(x + 1)**3"))
    assert r.ok and r.result == "x**3 + 3*x**2 + 3*x + 1"


def test_factor_quadratic():
    r = _drive(AlgebraArgs(op="factor", expression="x**2 - 5*x + 6"))
    assert r.ok
    assert r.result == "(x - 3)*(x - 2)" or r.result == "(x - 2)*(x - 3)"


def test_trigsimp_pythagorean():
    r = _drive(AlgebraArgs(op="trigsimp", expression="sin(x)**2 + cos(x)**2"))
    assert r.ok and r.result == "1"


# ── Calculus ──────────────────────────────────────────────────────────

def test_diff_of_sin_is_cos():
    r = _drive(AlgebraArgs(op="diff", expression="sin(x)", variable="x"))
    assert r.ok and r.result == "cos(x)"


def test_diff_polynomial():
    r = _drive(AlgebraArgs(op="diff", expression="x**3 + 2*x", variable="x"))
    assert r.ok and r.result == "3*x**2 + 2"


def test_indefinite_integral():
    r = _drive(AlgebraArgs(op="integrate", expression="cos(x)", variable="x"))
    assert r.ok and r.result == "sin(x)"


def test_definite_integral():
    r = _drive(AlgebraArgs(op="integrate", expression="x**2",
                           variable="x", a="0", b="1"))
    assert r.ok and r.result == "1/3"


def test_integrate_needs_both_bounds():
    """If only `a` is set (not `b`), refuse."""
    r = _drive(AlgebraArgs(op="integrate", expression="x", variable="x", a="0"))
    assert not r.ok
    assert "BOTH" in r.error or "definite" in r.error


def test_limit_sin_over_x_at_zero():
    r = _drive(AlgebraArgs(op="limit", expression="sin(x)/x",
                           variable="x", value="0"))
    assert r.ok and r.result == "1"


def test_limit_at_infinity():
    r = _drive(AlgebraArgs(op="limit", expression="1/x",
                           variable="x", value="oo"))
    assert r.ok and r.result == "0"


def test_taylor_series_of_exp():
    r = _drive(AlgebraArgs(op="series", expression="exp(x)",
                           variable="x", value="0", order=4))
    assert r.ok
    assert "x**3/6" in r.result and "x**2/2" in r.result


def test_evaluate_sin_at_pi_over_2():
    r = _drive(AlgebraArgs(op="evaluate", expression="sin(x)",
                           variable="x", value="pi/2"))
    assert r.ok and r.result == "1"


def test_evaluate_polynomial_at_value():
    r = _drive(AlgebraArgs(op="evaluate", expression="x**2 + 1",
                           variable="x", value="3"))
    assert r.ok and r.result == "10"


# ── Rational / partial fractions ───────────────────────────────────────

def test_apart_simple():
    r = _drive(AlgebraArgs(op="apart", expression="1/((x-1)*(x-2))",
                           variable="x"))
    assert r.ok
    # Either 1/(x-2) - 1/(x-1) or the reverse
    assert "1/(x - 1)" in r.result and "1/(x - 2)" in r.result


def test_together_combines():
    r = _drive(AlgebraArgs(op="together",
                           expression="1/(x-1) + 1/(x+1)"))
    assert r.ok
    assert "x**2" in r.result or "(x" in r.result


# ── Sandbox enforcement ────────────────────────────────────────────────

def test_rejects_import():
    r = _drive(AlgebraArgs(op="solve", expression="import os", variable="x"))
    assert not r.ok
    assert "forbidden" in r.error or "import" in r.error.lower()


def test_rejects_attribute_access():
    r = _drive(AlgebraArgs(op="simplify", expression="x.value"))
    assert not r.ok


def test_rejects_unknown_function():
    r = _drive(AlgebraArgs(op="simplify", expression="banana(x)"))
    assert not r.ok
    assert "unknown function" in r.error or "banana" in r.error


def test_rejects_empty_expression():
    r = _drive(AlgebraArgs(op="simplify", expression=""))
    assert not r.ok


def test_rejects_too_long():
    r = _drive(AlgebraArgs(op="simplify", expression="x " * 5000))
    assert not r.ok


# ── Discovery ──────────────────────────────────────────────────────────

def test_tool_is_discoverable():
    from drydock.core.paths import DEFAULT_TOOL_DIR
    from drydock.core.tools.manager import ToolManager
    classes = list(ToolManager._iter_tool_classes([DEFAULT_TOOL_DIR.path]))
    names = {c.get_name() for c in classes}
    assert "algebra" in names


def test_bad_variable_name_rejected():
    r = _drive(AlgebraArgs(op="diff", expression="x", variable="123bad"))
    assert not r.ok

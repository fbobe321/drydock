"""Tests for the solve built-in tool — Z3-backed constraint solver."""
from __future__ import annotations

import asyncio

import pytest

from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.solve_tool import (
    Solve,
    SolveArgs,
    SolveConfig,
    SolveResult,
    _declare_variables,
    _parse_expr,
)


def _drive(args: SolveArgs) -> SolveResult:
    tool = Solve(config=SolveConfig(), state=BaseToolState())

    async def go() -> SolveResult:
        result = None
        async for ev in tool.run(args):
            if isinstance(ev, SolveResult):
                result = ev
        assert result is not None
        return result

    return asyncio.run(go())


# ── solve ────────────────────────────────────────────────────────────────

def test_solve_linear_system():
    r = _drive(SolveArgs(
        op="solve",
        variables="x:Int, y:Int",
        constraints=["x + y == 10", "x - y == 4"],
    ))
    assert r.ok
    assert r.status == "sat"
    assert "x=7" in r.model and "y=3" in r.model


def test_solve_unsat():
    r = _drive(SolveArgs(
        op="solve",
        variables="x:Int",
        constraints=["x > 0", "x < 0"],
    ))
    assert r.ok
    assert r.status == "unsat"


def test_solve_mod_arithmetic():
    # 3x ≡ 5 (mod 7), 0 ≤ x < 7  →  x=4
    r = _drive(SolveArgs(
        op="solve",
        variables="x:Int",
        constraints=["x >= 0", "x < 7", "3*x % 7 == 5"],
    ))
    assert r.ok and r.status == "sat"
    assert "x=4" in r.model


def test_solve_with_boolean_var():
    r = _drive(SolveArgs(
        op="solve",
        variables="p:Bool, q:Bool",
        constraints=["Implies(p, q)", "p == True"],
    ))
    assert r.ok and r.status == "sat"
    assert "q=True" in r.model


def test_solve_distinct_constraint():
    # Three distinct positive ints, each at most 5
    r = _drive(SolveArgs(
        op="solve",
        variables="a:Int, b:Int, c:Int",
        constraints=["a >= 1", "b >= 1", "c >= 1",
                     "a <= 5", "b <= 5", "c <= 5",
                     "Distinct(a, b, c)"],
    ))
    assert r.ok and r.status == "sat"
    # Pull out values; they must all be different
    vals = [int(p.split("=")[1]) for p in r.model.split(", ")]
    assert len(set(vals)) == 3


# ── prove ────────────────────────────────────────────────────────────────

def test_prove_valid_implication():
    # If x > 0 then x + 1 > 1 — must be valid for all int x
    r = _drive(SolveArgs(
        op="prove",
        variables="x:Int",
        constraints=["x > 0"],
        conclusion="x + 1 > 1",
    ))
    assert r.ok and r.status == "valid"


def test_prove_returns_counterexample():
    r = _drive(SolveArgs(
        op="prove",
        variables="x:Int",
        constraints=["x > 0"],
        conclusion="x < 0",
    ))
    assert r.ok and r.status == "countered"
    assert r.model.startswith("x=")


def test_prove_needs_conclusion():
    r = _drive(SolveArgs(
        op="prove", variables="x:Int", constraints=["x > 0"],
    ))
    assert not r.ok
    assert "conclusion" in r.error


# ── find_all ─────────────────────────────────────────────────────────────

def test_find_all_enumerates_domain():
    r = _drive(SolveArgs(
        op="find_all",
        variables="x:Int",
        constraints=["x >= 1", "x <= 5"],
        limit=10,
    ))
    assert r.ok and r.status == "sat"
    assert len(r.models) == 5
    vals = sorted(int(m.split("=")[1]) for m in r.models)
    assert vals == [1, 2, 3, 4, 5]


def test_find_all_respects_limit():
    r = _drive(SolveArgs(
        op="find_all",
        variables="x:Int",
        constraints=["x >= 1", "x <= 100"],
        limit=3,
    ))
    assert r.ok and r.status == "sat"
    assert len(r.models) == 3


def test_find_all_no_solutions_is_unsat():
    r = _drive(SolveArgs(
        op="find_all",
        variables="x:Int",
        constraints=["x > 0", "x < 0"],
        limit=5,
    ))
    assert r.ok and r.status == "unsat"
    assert r.models == []


# ── optimize ─────────────────────────────────────────────────────────────

def test_optimize_max():
    # max x*y subject to x + y == 10, x ≥ 0, y ≥ 0 — peaks at x=y=5
    r = _drive(SolveArgs(
        op="optimize",
        variables="x:Int, y:Int",
        constraints=["x + y == 10", "x >= 0", "y >= 0"],
        objective="x * y",
        direction="max",
    ))
    assert r.ok and r.status == "optimal"
    assert r.objective_value == "25"
    assert "x=5" in r.model and "y=5" in r.model


def test_optimize_min():
    # min x subject to x*x >= 16, x >= 0 → x=4
    r = _drive(SolveArgs(
        op="optimize",
        variables="x:Int",
        constraints=["x * x >= 16", "x >= 0"],
        objective="x",
        direction="min",
    ))
    assert r.ok and r.status == "optimal"
    assert r.objective_value == "4"


def test_optimize_infeasible():
    r = _drive(SolveArgs(
        op="optimize",
        variables="x:Int",
        constraints=["x > 0", "x < 0"],
        objective="x",
        direction="min",
    ))
    assert r.ok and r.status == "infeasible"


def test_optimize_needs_objective():
    r = _drive(SolveArgs(
        op="optimize", variables="x:Int", constraints=["x > 0"],
    ))
    assert not r.ok
    assert "objective" in r.error


# ── Variable declaration ─────────────────────────────────────────────────

def test_declare_all_types():
    decl = _declare_variables("a:Int, b:Real, c:Bool, d:BitVec8")
    assert set(decl.keys()) == {"a", "b", "c", "d"}


def test_declare_rejects_bad_name():
    with pytest.raises(Exception):
        _declare_variables("123bad:Int")


def test_declare_rejects_duplicate():
    with pytest.raises(Exception):
        _declare_variables("x:Int, x:Real")


def test_declare_rejects_unknown_type():
    with pytest.raises(Exception):
        _declare_variables("x:String")


def test_declare_rejects_bad_bitvec_width():
    with pytest.raises(Exception):
        _declare_variables("x:BitVec128")  # >64


def test_declare_caps_variable_count():
    spec = ", ".join(f"v{i}:Int" for i in range(65))
    with pytest.raises(Exception):
        _declare_variables(spec)


def test_bitvec_arithmetic_solves():
    # 8-bit overflow: 200 + 100 wraps to 44 (300 mod 256)
    r = _drive(SolveArgs(
        op="solve",
        variables="x:BitVec8",
        constraints=["x == 200 + 100"],
    ))
    assert r.ok and r.status == "sat"
    # z3 prints bitvecs as ints; just check it returned something
    assert r.model.startswith("x=")


# ── Sandboxing ───────────────────────────────────────────────────────────

def test_blocks_attribute_access():
    r = _drive(SolveArgs(
        op="solve",
        variables="x:Int",
        constraints=["x.foo == 1"],
    ))
    assert not r.ok
    assert "attribute access" in r.error


def test_blocks_undeclared_name():
    r = _drive(SolveArgs(
        op="solve",
        variables="x:Int",
        constraints=["y > 0"],
    ))
    assert not r.ok
    assert "undeclared" in r.error


def test_blocks_unknown_function():
    r = _drive(SolveArgs(
        op="solve",
        variables="x:Int",
        constraints=["x == os(0)"],
    ))
    # `os` is treated as an undeclared name in the AST walker
    # (calls are validated against the same namespace).
    assert not r.ok


@pytest.mark.parametrize("bad", [
    "__import__('os')",
    "x.__class__",
    "exec('print(1)')",
    "open('/etc/passwd')",
    "globals()",
])
def test_forbidden_tokens_rejected(bad: str):
    r = _drive(SolveArgs(
        op="solve",
        variables="x:Int",
        constraints=[bad],
    ))
    assert not r.ok
    assert "forbidden" in r.error or "attribute" in r.error or "unknown" in r.error or "undeclared" in r.error


# ── Empty / malformed inputs ─────────────────────────────────────────────

def test_empty_variables_rejected():
    r = _drive(SolveArgs(op="solve", variables="", constraints=["1 == 1"]))
    assert not r.ok


def test_empty_constraints_for_solve_rejected():
    r = _drive(SolveArgs(op="solve", variables="x:Int", constraints=[]))
    assert not r.ok
    assert "constraint" in r.error


# ── Higher-level proof: a classic identity ───────────────────────────────

def test_prove_de_morgan_via_solver():
    # ¬(p ∧ q) ↔ (¬p ∨ ¬q) — should be valid
    r = _drive(SolveArgs(
        op="prove",
        variables="p:Bool, q:Bool",
        constraints=[],
        conclusion="Not(And(p, q)) == Or(Not(p), Not(q))",
    ))
    assert r.ok and r.status == "valid"


# ── Discovery ──────────────────────────────────────────────────────────

def test_tool_is_discoverable():
    """`solve` should be auto-registered by ToolManager scanning builtins."""
    from drydock.core.paths import DEFAULT_TOOL_DIR
    from drydock.core.tools.manager import ToolManager
    classes = list(ToolManager._iter_tool_classes([DEFAULT_TOOL_DIR.path]))
    names = {c.get_name() for c in classes}
    assert "solve" in names

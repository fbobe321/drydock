"""Tests for the linear_algebra built-in."""
from __future__ import annotations

import asyncio

import pytest

from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.linear_algebra_tool import (
    LinearAlgebra,
    LinearAlgebraArgs,
    LinearAlgebraConfig,
    LinearAlgebraResult,
)


def _drive(args: LinearAlgebraArgs) -> LinearAlgebraResult:
    tool = LinearAlgebra(config=LinearAlgebraConfig(), state=BaseToolState())

    async def go() -> LinearAlgebraResult:
        result = None
        async for ev in tool.run(args):
            if isinstance(ev, LinearAlgebraResult):
                result = ev
        assert result is not None
        return result

    return asyncio.run(go())


# ── Scalar properties ──────────────────────────────────────────────────

def test_determinant_2x2():
    """det([[1,2],[3,4]]) = 1·4 - 2·3 = -2"""
    r = _drive(LinearAlgebraArgs(op="determinant", matrix="1, 2; 3, 4"))
    assert r.ok and r.result == "-2"


def test_determinant_3x3():
    """det(I3) = 1"""
    r = _drive(LinearAlgebraArgs(
        op="determinant", matrix="1, 0, 0; 0, 1, 0; 0, 0, 1"))
    assert r.ok and r.result == "1"


def test_determinant_singular():
    """det of singular = 0"""
    r = _drive(LinearAlgebraArgs(op="determinant", matrix="1, 2; 2, 4"))
    assert r.ok and r.result == "0"


def test_determinant_requires_square():
    r = _drive(LinearAlgebraArgs(op="determinant", matrix="1, 2, 3; 4, 5, 6"))
    assert not r.ok
    assert "square" in r.error


def test_trace_2x2():
    """tr([[1,2],[3,4]]) = 1 + 4 = 5"""
    r = _drive(LinearAlgebraArgs(op="trace", matrix="1, 2; 3, 4"))
    assert r.ok and r.result == "5"


def test_rank_singular_2x2():
    r = _drive(LinearAlgebraArgs(op="rank", matrix="1, 2; 2, 4"))
    assert r.ok and r.result == "1"


def test_rank_full_2x2():
    r = _drive(LinearAlgebraArgs(op="rank", matrix="1, 0; 0, 1"))
    assert r.ok and r.result == "2"


def test_shape():
    r = _drive(LinearAlgebraArgs(op="shape", matrix="1, 2, 3; 4, 5, 6"))
    assert r.ok and r.result == "(2, 3)"


# ── Matrix properties ──────────────────────────────────────────────────

def test_transpose_2x3():
    r = _drive(LinearAlgebraArgs(op="transpose", matrix="1, 2, 3; 4, 5, 6"))
    assert r.ok and r.result == "[[1, 4], [2, 5], [3, 6]]"


def test_inverse_2x2():
    """[[1,2],[3,4]]^-1 = [[-2,1],[3/2,-1/2]]"""
    r = _drive(LinearAlgebraArgs(op="inverse", matrix="1, 2; 3, 4"))
    assert r.ok
    assert "-2" in r.result and "1/2" in r.result


def test_inverse_singular_fails():
    r = _drive(LinearAlgebraArgs(op="inverse", matrix="1, 2; 2, 4"))
    assert not r.ok
    assert "invertible" in r.error or "not invert" in r.error


def test_rref_identifies_pivots():
    r = _drive(LinearAlgebraArgs(op="rref", matrix="1, 2; 3, 7"))
    assert r.ok
    assert "pivots=[0, 1]" in r.result


def test_nullspace_trivial():
    r = _drive(LinearAlgebraArgs(op="nullspace", matrix="1, 0; 0, 1"))
    assert r.ok
    assert "trivial" in r.result or r.result == "[]"


def test_nullspace_nontrivial():
    """[[1,2],[2,4]] has nullspace spanned by [-2, 1]."""
    r = _drive(LinearAlgebraArgs(op="nullspace", matrix="1, 2; 2, 4"))
    assert r.ok
    assert "-2" in r.result


# ── Eigenvalues ────────────────────────────────────────────────────────

def test_eigenvals_diagonal_matrix():
    r = _drive(LinearAlgebraArgs(op="eigenvals", matrix="2, 0; 0, 3"))
    assert r.ok
    assert "2" in r.result and "3" in r.result


def test_eigenvals_upper_triangular():
    """Eigenvalues of upper-triangular are the diagonal."""
    r = _drive(LinearAlgebraArgs(op="eigenvals", matrix="2, 1; 0, 3"))
    assert r.ok
    assert "2" in r.result and "3" in r.result


def test_eigenvects_returns_full_decomposition():
    r = _drive(LinearAlgebraArgs(op="eigenvects", matrix="2, 0; 0, 3"))
    assert r.ok
    assert "λ" in r.result and "mult" in r.result


# ── Arithmetic ────────────────────────────────────────────────────────

def test_multiply_2x2():
    r = _drive(LinearAlgebraArgs(
        op="multiply", matrix="1, 2; 3, 4", matrix2="5, 6; 7, 8"))
    assert r.ok and r.result == "[[19, 22], [43, 50]]"


def test_multiply_shape_mismatch():
    """A: 2×3, B: 2×2 — A·B undefined because A.cols=3 ≠ B.rows=2."""
    r = _drive(LinearAlgebraArgs(
        op="multiply", matrix="1, 2, 3; 4, 5, 6", matrix2="1, 2; 3, 4"))
    assert not r.ok
    assert "shape" in r.error or "mismatch" in r.error


def test_add():
    r = _drive(LinearAlgebraArgs(
        op="add", matrix="1, 0; 0, 1", matrix2="2, 3; 4, 5"))
    assert r.ok and r.result == "[[3, 3], [4, 6]]"


def test_add_shape_mismatch():
    r = _drive(LinearAlgebraArgs(
        op="add", matrix="1, 2", matrix2="1, 2; 3, 4"))
    assert not r.ok


def test_scalar_mul():
    r = _drive(LinearAlgebraArgs(
        op="scalar_mul", matrix="1, 2; 3, 4", scalar="2"))
    assert r.ok and r.result == "[[2, 4], [6, 8]]"


def test_solve_linear_diagonal():
    r = _drive(LinearAlgebraArgs(
        op="solve_linear", matrix="2, 0; 0, 3", vector="4; 6"))
    assert r.ok and r.result == "[[2], [2]]"


def test_solve_linear_general():
    r = _drive(LinearAlgebraArgs(
        op="solve_linear", matrix="1, 1; 1, -1", vector="5; 1"))
    assert r.ok
    # x + y = 5, x - y = 1 ⇒ x=3, y=2
    assert "3" in r.result and "2" in r.result


def test_power_5_of_upper_triangular():
    """[[1,1],[0,1]]^n = [[1,n],[0,1]] (Jordan block)."""
    r = _drive(LinearAlgebraArgs(
        op="power", matrix="1, 1; 0, 1", n="5"))
    assert r.ok and r.result == "[[1, 5], [0, 1]]"


def test_power_negative_requires_invertible():
    r = _drive(LinearAlgebraArgs(
        op="power", matrix="1, 1; 0, 1", n="-1"))
    assert r.ok  # this one is invertible
    # A^-1 = [[1,-1],[0,1]]
    assert "-1" in r.result


# ── Symbolic entries ───────────────────────────────────────────────────

def test_symbolic_determinant():
    """det([[x,1],[1,x]]) = x² - 1"""
    r = _drive(LinearAlgebraArgs(op="determinant", matrix="x, 1; 1, x"))
    assert r.ok and r.result == "x**2 - 1"


# ── Sandbox + caps ─────────────────────────────────────────────────────

def test_rejects_import():
    r = _drive(LinearAlgebraArgs(op="trace", matrix="import os; 0"))
    assert not r.ok


def test_rejects_too_large_matrix():
    """9×9 exceeds 8×8 cap."""
    row = ",".join("1" for _ in range(9))
    big = ";".join(row for _ in range(9))
    r = _drive(LinearAlgebraArgs(op="trace", matrix=big))
    assert not r.ok
    assert "cap" in r.error.lower() or "exceeds" in r.error


def test_rejects_uneven_rows():
    r = _drive(LinearAlgebraArgs(op="trace", matrix="1, 2; 3"))
    assert not r.ok


# ── Discovery ──────────────────────────────────────────────────────────

def test_tool_is_discoverable():
    from drydock.core.paths import DEFAULT_TOOL_DIR
    from drydock.core.tools.manager import ToolManager
    classes = list(ToolManager._iter_tool_classes([DEFAULT_TOOL_DIR.path]))
    names = {c.get_name() for c in classes}
    assert "linear_algebra" in names

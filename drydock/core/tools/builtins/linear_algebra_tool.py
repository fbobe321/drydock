"""Linear algebra tool — matrix operations via sympy.Matrix.

Fifth addition to the symbolic-math stack. HLE Math + Physics both
have linear-algebra problems: eigenvalues of small matrices,
determinants for cross-product / area computations, solving
Ax = b for symbolic A, computing rank / nullspace, RREF.

Backend: sympy.Matrix (exact symbolic). Same sandboxing as the
other symbolic tools.

Operations (`op=`):

  determinant(matrix)              — det(A)
  inverse(matrix)                  — A^(-1)  (raises if singular)
  transpose(matrix)                — A^T
  trace(matrix)                    — tr(A) = Σ A[i,i]
  rank(matrix)                     — rank of A
  rref(matrix)                     — reduced row-echelon form
  nullspace(matrix)                — basis for ker(A)
  eigenvals(matrix)                — eigenvalues with multiplicities
  eigenvects(matrix)               — full (λ, mult, [eigvecs]) list
  multiply(matrix, matrix2)        — A · B
  add(matrix, matrix2)             — A + B
  scalar_mul(matrix, scalar)       — k · A   (scalar parsed as a sympy expr)
  solve_linear(matrix, vector)     — solve A x = b ; matrix=A, vector=b (col)
  shape(matrix)                    — (rows, cols)
  power(matrix, n)                 — A^n (n is integer; negative requires invertible)

Matrix input syntax: row-major, semicolons separate rows, commas
separate entries. Examples:
  matrix="1, 2; 3, 4"          → [[1, 2], [3, 4]]
  matrix="0, 1; -1, 0"         → 2D rotation by 90°
  matrix="x, 1; 1, x"          → entries can be symbolic
  vector="5; 6"                → column vector

Sandboxed; max 8×8 matrices.
"""
from __future__ import annotations

import ast as _ast
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, ClassVar, Literal, final

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from drydock.core.tools.ui import (
    ToolCallDisplay,
    ToolResultDisplay,
    ToolUIData,
)
from drydock.core.types import ToolStreamEvent

if TYPE_CHECKING:
    from drydock.core.types import ToolResultEvent


LinearAlgebraOp = Literal[
    "determinant", "inverse", "transpose", "trace", "rank",
    "rref", "nullspace", "eigenvals", "eigenvects",
    "multiply", "add", "scalar_mul",
    "solve_linear", "shape", "power",
]


class LinearAlgebraArgs(BaseModel):
    op: LinearAlgebraOp = Field(
        description=(
            "Operation: determinant | inverse | transpose | trace | rank | "
            "rref | nullspace | eigenvals | eigenvects | multiply | add | "
            "scalar_mul | solve_linear | shape | power."
        )
    )
    matrix: str = Field(
        default="",
        description=(
            "Primary matrix. Semicolon-separated rows, comma-separated "
            "entries. Each entry is a sympy expression (numbers / "
            "symbols / sin / cos / ...). Example: '1, 2; 3, 4' is a 2×2 "
            "matrix. Symbolic entries OK: 'x, 1; 1, x'."
        ),
    )
    matrix2: str = Field(
        default="",
        description=(
            "Second matrix for binary ops (multiply, add). Same syntax."
        ),
    )
    vector: str = Field(
        default="",
        description="Column vector for solve_linear. Same syntax (one column).",
    )
    scalar: str = Field(
        default="",
        description="Scalar for scalar_mul. Any sympy expression (e.g. '2', 'pi', 'k').",
    )
    n: str = Field(
        default="",
        description="Power for `power` op (integer, can be negative).",
    )


class LinearAlgebraResult(BaseModel):
    ok: bool
    op: str = ""
    result: str = ""
    result_type: str = ""
    error: str = ""


class LinearAlgebraConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


# ── Expression / matrix parsing ───────────────────────────────────────

_BAD_PATTERNS = (
    "__", "import", "exec", "eval", "open",
    "globals", "locals", "getattr", "setattr",
)
_MAX_DIM = 8
_ENTRY_GLOBALS: dict[str, Any] = {}


def _entry_globals() -> dict[str, Any]:
    global _ENTRY_GLOBALS
    if _ENTRY_GLOBALS:
        return _ENTRY_GLOBALS
    import sympy
    _ENTRY_GLOBALS = {
        "__builtins__": {},
        "Symbol": sympy.Symbol, "Rational": sympy.Rational,
        "pi": sympy.pi, "E": sympy.E, "I": sympy.I, "oo": sympy.oo,
        "sin": sympy.sin, "cos": sympy.cos, "tan": sympy.tan,
        "log": sympy.log, "exp": sympy.exp, "sqrt": sympy.sqrt,
        "Abs": sympy.Abs, "factorial": sympy.factorial,
    }
    return _ENTRY_GLOBALS


def _parse_entry(s: str):
    s = s.strip()
    if not s:
        raise ToolError("empty matrix entry")
    if len(s) > 300:
        raise ToolError(f"matrix entry too long (>300 chars): {s[:30]!r}")
    lower = s.lower()
    for bad in _BAD_PATTERNS:
        if bad in lower:
            raise ToolError(f"forbidden token in matrix entry: {bad!r}")
    import sympy
    try:
        tree = _ast.parse(s, mode="eval")
    except SyntaxError as e:
        raise ToolError(f"entry SyntaxError: {e.msg}") from e
    safe_g = dict(_entry_globals())
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Attribute):
            raise ToolError("attribute access not permitted in matrix entry")
        if isinstance(node, _ast.Call):
            fn = getattr(node.func, "id", None)
            if fn is None or fn not in safe_g:
                raise ToolError(f"unknown function {fn!r} in matrix entry")
        if isinstance(node, _ast.Name):
            if node.id not in safe_g:
                safe_g[node.id] = sympy.Symbol(node.id)
    try:
        return eval(  # noqa: S307 — whitelisted namespace
            compile(tree, "<la-entry>", "eval"), safe_g, {}
        )
    except (SyntaxError, ValueError, TypeError) as e:
        raise ToolError(f"{type(e).__name__}: {e}") from e


def _parse_matrix(s: str, *, name: str = "matrix"):
    """Parse `r1c1, r1c2; r2c1, r2c2; ...` into a sympy Matrix."""
    if not s.strip():
        raise ToolError(f"{name} is empty")
    if len(s) > 4000:
        raise ToolError(f"{name} too long (>4000 chars)")
    rows = []
    for row_str in s.split(";"):
        row_str = row_str.strip()
        if not row_str:
            continue
        entries = [_parse_entry(e) for e in row_str.split(",")]
        rows.append(entries)
    if not rows:
        raise ToolError(f"{name}: no rows parsed")
    cols = len(rows[0])
    for i, r in enumerate(rows):
        if len(r) != cols:
            raise ToolError(
                f"{name}: row {i} has {len(r)} entries, expected {cols}"
            )
    if len(rows) > _MAX_DIM or cols > _MAX_DIM:
        raise ToolError(
            f"{name}: {len(rows)}×{cols} exceeds {_MAX_DIM}×{_MAX_DIM} cap"
        )
    import sympy
    return sympy.Matrix(rows)


def _fmt_matrix(M) -> str:
    """One-line matrix repr — `[[1, 2], [3, 4]]` style."""
    return str(M.tolist())


# ── Op implementations ───────────────────────────────────────────────

def _op_determinant(args: LinearAlgebraArgs) -> tuple[str, str]:
    M = _parse_matrix(args.matrix)
    if M.rows != M.cols:
        raise ToolError(f"determinant needs a square matrix, got {M.rows}×{M.cols}")
    return (str(M.det()), "expr")


def _op_inverse(args: LinearAlgebraArgs) -> tuple[str, str]:
    M = _parse_matrix(args.matrix)
    if M.rows != M.cols:
        raise ToolError(f"inverse needs a square matrix, got {M.rows}×{M.cols}")
    try:
        return (_fmt_matrix(M.inv()), "matrix")
    except Exception as e:
        raise ToolError(f"matrix not invertible: {e}") from e


def _op_transpose(args: LinearAlgebraArgs) -> tuple[str, str]:
    return (_fmt_matrix(_parse_matrix(args.matrix).T), "matrix")


def _op_trace(args: LinearAlgebraArgs) -> tuple[str, str]:
    M = _parse_matrix(args.matrix)
    if M.rows != M.cols:
        raise ToolError(f"trace needs a square matrix, got {M.rows}×{M.cols}")
    return (str(M.trace()), "expr")


def _op_rank(args: LinearAlgebraArgs) -> tuple[str, str]:
    return (str(_parse_matrix(args.matrix).rank()), "int")


def _op_rref(args: LinearAlgebraArgs) -> tuple[str, str]:
    M, pivots = _parse_matrix(args.matrix).rref()
    return (f"{_fmt_matrix(M)}  pivots={list(pivots)}", "matrix")


def _op_nullspace(args: LinearAlgebraArgs) -> tuple[str, str]:
    basis = _parse_matrix(args.matrix).nullspace()
    if not basis:
        return ("[]  (trivial kernel)", "list")
    return (str([_fmt_matrix(v) for v in basis]), "list_of_vectors")


def _op_eigenvals(args: LinearAlgebraArgs) -> tuple[str, str]:
    M = _parse_matrix(args.matrix)
    if M.rows != M.cols:
        raise ToolError(f"eigenvals needs a square matrix, got {M.rows}×{M.cols}")
    return (str(M.eigenvals()), "dict")  # {lambda: multiplicity}


def _op_eigenvects(args: LinearAlgebraArgs) -> tuple[str, str]:
    M = _parse_matrix(args.matrix)
    if M.rows != M.cols:
        raise ToolError(f"eigenvects needs a square matrix, got {M.rows}×{M.cols}")
    out = []
    for ev, mult, vects in M.eigenvects():
        out.append(f"λ={ev}  mult={mult}  vecs={[_fmt_matrix(v) for v in vects]}")
    return ("\n".join(out), "list")


def _op_multiply(args: LinearAlgebraArgs) -> tuple[str, str]:
    A = _parse_matrix(args.matrix, name="matrix")
    B = _parse_matrix(args.matrix2, name="matrix2")
    if A.cols != B.rows:
        raise ToolError(
            f"multiply: shape mismatch {A.rows}×{A.cols} · {B.rows}×{B.cols}"
        )
    return (_fmt_matrix(A * B), "matrix")


def _op_add(args: LinearAlgebraArgs) -> tuple[str, str]:
    A = _parse_matrix(args.matrix, name="matrix")
    B = _parse_matrix(args.matrix2, name="matrix2")
    if A.shape != B.shape:
        raise ToolError(f"add: shape mismatch {A.shape} vs {B.shape}")
    return (_fmt_matrix(A + B), "matrix")


def _op_scalar_mul(args: LinearAlgebraArgs) -> tuple[str, str]:
    if not args.scalar:
        raise ToolError("scalar_mul needs `scalar=`")
    k = _parse_entry(args.scalar)
    M = _parse_matrix(args.matrix)
    return (_fmt_matrix(k * M), "matrix")


def _op_solve_linear(args: LinearAlgebraArgs) -> tuple[str, str]:
    """Solve Ax = b.  matrix=A, vector=b (one-column matrix or 1×n row)."""
    A = _parse_matrix(args.matrix, name="matrix")
    if not args.vector:
        raise ToolError("solve_linear needs `vector=` (the RHS b)")
    b = _parse_matrix(args.vector, name="vector")
    # Accept either column vector (n×1) or row vector (1×n) — coerce to column.
    if b.shape[1] != 1:
        if b.shape[0] == 1:
            b = b.T
        else:
            raise ToolError(f"vector must be a column or row, got {b.shape}")
    if A.rows != b.rows:
        raise ToolError(
            f"solve_linear: A has {A.rows} rows but b has {b.rows}"
        )
    try:
        x = A.solve(b)
    except Exception as e:
        raise ToolError(f"no unique solution: {e}") from e
    return (_fmt_matrix(x), "matrix")


def _op_shape(args: LinearAlgebraArgs) -> tuple[str, str]:
    M = _parse_matrix(args.matrix)
    return (f"({M.rows}, {M.cols})", "tuple")


def _op_power(args: LinearAlgebraArgs) -> tuple[str, str]:
    M = _parse_matrix(args.matrix)
    if M.rows != M.cols:
        raise ToolError(f"power needs a square matrix, got {M.rows}×{M.cols}")
    if not args.n:
        raise ToolError("power needs `n=` (integer exponent)")
    try:
        n = int(args.n)
    except ValueError as e:
        raise ToolError(f"n must be an integer: {e}") from e
    if n < -20 or n > 20:
        raise ToolError("power: |n| capped at 20")
    return (_fmt_matrix(M ** n), "matrix")


_DISPATCH = {
    "determinant":  _op_determinant,
    "inverse":      _op_inverse,
    "transpose":    _op_transpose,
    "trace":        _op_trace,
    "rank":         _op_rank,
    "rref":         _op_rref,
    "nullspace":    _op_nullspace,
    "eigenvals":    _op_eigenvals,
    "eigenvects":   _op_eigenvects,
    "multiply":     _op_multiply,
    "add":          _op_add,
    "scalar_mul":   _op_scalar_mul,
    "solve_linear": _op_solve_linear,
    "shape":        _op_shape,
    "power":        _op_power,
}


class LinearAlgebra(
    BaseTool[LinearAlgebraArgs, LinearAlgebraResult, LinearAlgebraConfig, BaseToolState],
    ToolUIData[LinearAlgebraArgs, LinearAlgebraResult],
):
    description: ClassVar[str] = (
        "Linear algebra via sympy.Matrix — determinant, inverse, transpose, "
        "trace, rank, RREF, nullspace, eigenvalues, eigenvectors, matrix "
        "multiply/add, scalar multiplication, solving Ax = b, matrix powers. "
        "Matrix syntax: `1,2;3,4` (semicolon between rows, comma between "
        "entries). Symbolic entries OK: `x,1;1,x`. Max 8×8. Use INSTEAD "
        "of computing matrix products / eigenvalues by hand."
    )

    @classmethod
    def format_call_display(cls, args: LinearAlgebraArgs) -> ToolCallDisplay:
        m = args.matrix.strip()
        if len(m) > 30:
            m = m[:27] + "..."
        return ToolCallDisplay(summary=f"linear_algebra[{args.op}]: {m}")

    @classmethod
    def get_result_display(cls, event: "ToolResultEvent") -> ToolResultDisplay:
        if isinstance(event.result, LinearAlgebraResult):
            if not event.result.ok:
                return ToolResultDisplay(
                    success=False, message=f"la: {event.result.error[:80]}"
                )
            preview = event.result.result.split("\n", 1)[0]
            if len(preview) > 80:
                preview = preview[:77] + "..."
            return ToolResultDisplay(success=True, message=f"= {preview}")
        return ToolResultDisplay(success=True, message="la complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Computing"

    def resolve_permission(self, args: LinearAlgebraArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: LinearAlgebraArgs, ctx: "InvokeContext | None" = None
    ) -> AsyncGenerator["ToolStreamEvent | LinearAlgebraResult", None]:
        handler = _DISPATCH.get(args.op)
        if handler is None:
            yield LinearAlgebraResult(
                ok=False, op=args.op,
                error=f"unknown op {args.op!r}. Allowed: {list(_DISPATCH)}",
            )
            return
        try:
            value, rtype = handler(args)
        except ToolError as e:
            yield LinearAlgebraResult(ok=False, op=args.op, error=str(e))
            return
        except Exception as e:  # noqa: BLE001
            yield LinearAlgebraResult(
                ok=False, op=args.op, error=f"{type(e).__name__}: {e}"
            )
            return
        yield LinearAlgebraResult(
            ok=True, op=args.op, result=value, result_type=rtype
        )

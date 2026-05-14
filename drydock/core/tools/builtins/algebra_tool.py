"""Algebra tool — symbolic math (CAS) via sympy.

Companion to math_tool (arithmetic) and logic_tool (propositional logic).
This one handles the CAS operations that come up in HLE Math but that
the model gets wrong from prior alone: solving equations, integrating,
differentiating, simplifying, expanding, factoring, computing limits,
and generating Taylor series.

Sandbox: same shape as logic_tool — Python expressions, sympy
namespace, no imports/attribute access/dunder anything.

Operations (`op=`):

  solve(equation, var)       — solve eq=0 (or `lhs == rhs`) for var
  simplify(expression)       — sympy.simplify
  expand(expression)         — expand products
  factor(expression)         — factor polynomial
  diff(expression, var)      — d/dvar of expression
  integrate(expr, var, a, b) — integrate; pass a,b for definite, omit for indefinite
  limit(expression, var, point) — limit as var → point
  series(expression, var, point, n) — Taylor series to order n
  evaluate(expression, var, value) — substitute and simplify
  collect(expression, var)   — collect powers of var
  apart(expression)          — partial fraction decomposition
  together(expression)       — combine fractions
  trigsimp(expression)       — trig-specific simplification

Expression syntax: Python-style with sympy symbols.
  - Variables: any name (x, y, n, theta, ...) auto-becomes Symbol
  - Constants: pi, E, oo (infinity), I, nan
  - Functions: sin, cos, tan, log, exp, sqrt, Abs, factorial, ...
  - Operators: + - * / ** (no integer division - use Rational for fractions)

Read-only, side-effect free, ALWAYS permission.
"""
from __future__ import annotations

import ast as _ast
import re
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


AlgebraOp = Literal[
    "solve",
    "simplify",
    "expand",
    "factor",
    "diff",
    "integrate",
    "limit",
    "series",
    "evaluate",
    "collect",
    "apart",
    "together",
    "trigsimp",
]


_OP_HELP = (
    "Operation: solve | simplify | expand | factor | diff | integrate | "
    "limit | series | evaluate | collect | apart | together | trigsimp. "
    "See the tool docstring for what each one expects."
)


class AlgebraArgs(BaseModel):
    op: AlgebraOp = Field(description=_OP_HELP)
    expression: str = Field(
        description=(
            "Symbolic expression. Python-style: `x**2 + 2*x + 1` or "
            "`sin(theta)/cos(theta)` or `log(x) - 1`. Variables become "
            "sympy Symbols automatically. Constants: pi, E, oo, I. "
            "Functions: sin, cos, tan, asin, log, exp, sqrt, factorial, "
            "binomial(n,k), Rational(p,q), Abs."
        )
    )
    variable: str = Field(
        default="x",
        description=(
            "Free variable name (default 'x'). Used by solve/diff/"
            "integrate/limit/series/evaluate/collect."
        ),
    )
    value: str = Field(
        default="",
        description=(
            "For `evaluate`: the value to substitute (e.g. '2', 'pi/4', "
            "'sqrt(3)'). For `limit`: the point to approach (e.g. 'oo', "
            "'0', '1'). For `series`: the expansion point (default 0)."
        ),
    )
    a: str = Field(
        default="",
        description=(
            "Lower bound for definite integration. If empty, integrate "
            "indefinitely. Format: any sympy expression like '0', 'pi/2'."
        ),
    )
    b: str = Field(
        default="",
        description="Upper bound for definite integration. Required if `a` set.",
    )
    order: int = Field(
        default=6,
        ge=1,
        le=20,
        description="Order for `series` expansion (default 6).",
    )


class AlgebraResult(BaseModel):
    ok: bool
    op: str = ""
    result: str = ""
    result_type: str = ""
    error: str = ""


class AlgebraConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


# ── Expression parser ─────────────────────────────────────────────────

_BAD_PATTERNS = (
    "__", "import", "exec", "eval", "open",
    "globals", "locals", "getattr", "setattr",
)
_ALLOWED_NAMES: dict[str, Any] = {}


def _safe_globals() -> dict[str, Any]:
    global _ALLOWED_NAMES
    if _ALLOWED_NAMES:
        return _ALLOWED_NAMES
    import sympy
    _ALLOWED_NAMES = {
        "__builtins__": {},
        # Core symbols & constants
        "Symbol": sympy.Symbol,
        "Rational": sympy.Rational,
        "Integer": sympy.Integer,
        "Float": sympy.Float,
        "pi": sympy.pi, "E": sympy.E, "oo": sympy.oo,
        "I": sympy.I, "nan": sympy.nan,
        # Functions
        "sin": sympy.sin, "cos": sympy.cos, "tan": sympy.tan,
        "asin": sympy.asin, "acos": sympy.acos, "atan": sympy.atan,
        "sinh": sympy.sinh, "cosh": sympy.cosh, "tanh": sympy.tanh,
        "log": sympy.log, "ln": sympy.log, "exp": sympy.exp,
        "sqrt": sympy.sqrt, "Abs": sympy.Abs,
        "factorial": sympy.factorial, "binomial": sympy.binomial,
        "floor": sympy.floor, "ceiling": sympy.ceiling,
        "gamma": sympy.gamma, "erf": sympy.erf,
        "Sum": sympy.Sum, "Product": sympy.Product,
        # Constructors that come up
        "Eq": sympy.Eq, "Ne": sympy.Ne, "Lt": sympy.Lt, "Gt": sympy.Gt,
        "Le": sympy.Le, "Ge": sympy.Ge,
        "Matrix": sympy.Matrix,
    }
    return _ALLOWED_NAMES


def _validate(expr: str) -> None:
    if not expr or not expr.strip():
        raise ToolError("expression is empty")
    if len(expr) > 4000:
        raise ToolError("expression too long (>4000 chars)")
    lower = expr.lower()
    for bad in _BAD_PATTERNS:
        if bad in lower:
            raise ToolError(f"forbidden token in expression: {bad!r}")


def _parse(expr: str):
    _validate(expr)
    import sympy
    try:
        tree = _ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ToolError(f"SyntaxError: {e.msg} at column {e.offset or '?'}") from e
    safe_g = dict(_safe_globals())
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Attribute):
            raise ToolError("attribute access not permitted")
        if isinstance(node, _ast.Call):
            fn = getattr(node.func, "id", None)
            if fn is None or fn not in safe_g:
                raise ToolError(
                    f"unknown function {fn!r} (whitelist: "
                    f"{sorted(k for k in safe_g if k != '__builtins__')[:20]}...)"
                )
        if isinstance(node, _ast.Name):
            n = node.id
            if n not in safe_g:
                safe_g[n] = sympy.Symbol(n)
    try:
        return eval(  # noqa: S307 — whitelisted namespace
            compile(tree, "<algebra>", "eval"), safe_g, {}
        )
    except (SyntaxError, ValueError, TypeError) as e:
        raise ToolError(f"{type(e).__name__}: {e}") from e


def _sym(name: str):
    import sympy
    if not name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ToolError(f"bad variable name {name!r}")
    return sympy.Symbol(name)


# ── Op implementations ───────────────────────────────────────────────

def _op_solve(args: AlgebraArgs) -> tuple[str, str]:
    import sympy
    e = _parse(args.expression)
    v = _sym(args.variable)
    # Accept either an equation (Eq) or an expression (assumed = 0)
    if not isinstance(e, sympy.Equality):
        e = sympy.Eq(e, 0)
    sol = sympy.solve(e, v)
    return (str(sol), "list" if isinstance(sol, list) else "expr")


def _op_simplify(args: AlgebraArgs) -> tuple[str, str]:
    import sympy
    return (str(sympy.simplify(_parse(args.expression))), "expr")


def _op_expand(args: AlgebraArgs) -> tuple[str, str]:
    import sympy
    return (str(sympy.expand(_parse(args.expression))), "expr")


def _op_factor(args: AlgebraArgs) -> tuple[str, str]:
    import sympy
    return (str(sympy.factor(_parse(args.expression))), "expr")


def _op_diff(args: AlgebraArgs) -> tuple[str, str]:
    import sympy
    return (str(sympy.diff(_parse(args.expression), _sym(args.variable))), "expr")


def _op_integrate(args: AlgebraArgs) -> tuple[str, str]:
    import sympy
    expr = _parse(args.expression)
    v = _sym(args.variable)
    if args.a and args.b:
        a_ = _parse(args.a) if args.a else 0
        b_ = _parse(args.b) if args.b else 0
        return (str(sympy.integrate(expr, (v, a_, b_))), "expr")
    if args.a or args.b:
        raise ToolError("integrate needs BOTH `a` and `b` for definite integration, or neither for indefinite")
    return (str(sympy.integrate(expr, v)), "expr")


def _op_limit(args: AlgebraArgs) -> tuple[str, str]:
    import sympy
    if not args.value:
        raise ToolError("limit needs `value=` for the point to approach")
    point = _parse(args.value)
    return (str(sympy.limit(_parse(args.expression), _sym(args.variable), point)), "expr")


def _op_series(args: AlgebraArgs) -> tuple[str, str]:
    import sympy
    point = _parse(args.value) if args.value else 0
    return (
        str(sympy.series(_parse(args.expression), _sym(args.variable), point, args.order).removeO()),
        "expr",
    )


def _op_evaluate(args: AlgebraArgs) -> tuple[str, str]:
    import sympy
    if not args.value:
        raise ToolError("evaluate needs `value=` to substitute")
    expr = _parse(args.expression)
    val = _parse(args.value)
    return (str(sympy.simplify(expr.subs(_sym(args.variable), val))), "expr")


def _op_collect(args: AlgebraArgs) -> tuple[str, str]:
    import sympy
    return (str(sympy.collect(_parse(args.expression), _sym(args.variable))), "expr")


def _op_apart(args: AlgebraArgs) -> tuple[str, str]:
    import sympy
    return (str(sympy.apart(_parse(args.expression), _sym(args.variable))), "expr")


def _op_together(args: AlgebraArgs) -> tuple[str, str]:
    import sympy
    return (str(sympy.together(_parse(args.expression))), "expr")


def _op_trigsimp(args: AlgebraArgs) -> tuple[str, str]:
    import sympy
    return (str(sympy.trigsimp(_parse(args.expression))), "expr")


_DISPATCH = {
    "solve":     _op_solve,
    "simplify":  _op_simplify,
    "expand":    _op_expand,
    "factor":    _op_factor,
    "diff":      _op_diff,
    "integrate": _op_integrate,
    "limit":     _op_limit,
    "series":    _op_series,
    "evaluate":  _op_evaluate,
    "collect":   _op_collect,
    "apart":     _op_apart,
    "together":  _op_together,
    "trigsimp":  _op_trigsimp,
}


class Algebra(
    BaseTool[AlgebraArgs, AlgebraResult, AlgebraConfig, BaseToolState],
    ToolUIData[AlgebraArgs, AlgebraResult],
):
    description: ClassVar[str] = (
        "Symbolic algebra via sympy — solve equations, simplify, expand, "
        "factor, differentiate, integrate, take limits, generate Taylor "
        "series, substitute values. Use INSTEAD of doing CAS work in "
        "your head when the answer needs to be exact. Variables auto-bind "
        "to sympy Symbols, so `x**2 - 4` and `sin(theta)/cos(theta)` "
        "just work. Functions available: sin/cos/tan, log/exp/sqrt, "
        "factorial, binomial, gamma, erf, Sum/Product. Constants: pi, "
        "E, oo (infinity), I, Rational(p,q). Sandboxed — no imports."
    )

    @classmethod
    def format_call_display(cls, args: AlgebraArgs) -> ToolCallDisplay:
        e = args.expression.strip()
        if len(e) > 40:
            e = e[:37] + "..."
        return ToolCallDisplay(summary=f"algebra[{args.op}]: {e}")

    @classmethod
    def get_result_display(cls, event: "ToolResultEvent") -> ToolResultDisplay:
        if isinstance(event.result, AlgebraResult):
            if not event.result.ok:
                return ToolResultDisplay(
                    success=False, message=f"algebra: {event.result.error[:80]}"
                )
            preview = event.result.result
            if len(preview) > 80:
                preview = preview[:77] + "..."
            return ToolResultDisplay(success=True, message=f"= {preview}")
        return ToolResultDisplay(success=True, message="algebra complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Computing symbolically"

    def resolve_permission(self, args: AlgebraArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: AlgebraArgs, ctx: "InvokeContext | None" = None
    ) -> AsyncGenerator["ToolStreamEvent | AlgebraResult", None]:
        handler = _DISPATCH.get(args.op)
        if handler is None:
            yield AlgebraResult(
                ok=False, op=args.op,
                error=f"unknown op {args.op!r}. Allowed: {list(_DISPATCH)}",
            )
            return
        try:
            value, rtype = handler(args)
        except ToolError as e:
            yield AlgebraResult(ok=False, op=args.op, error=str(e))
            return
        except Exception as e:  # noqa: BLE001 — surface to model
            yield AlgebraResult(
                ok=False, op=args.op, error=f"{type(e).__name__}: {e}"
            )
            return
        yield AlgebraResult(ok=True, op=args.op, result=value, result_type=rtype)

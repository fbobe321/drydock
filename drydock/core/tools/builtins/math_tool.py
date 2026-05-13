"""Math tool — exact arithmetic, statistics, and number theory via the
Python stdlib, exposed as a single direct built-in (NOT an MCP server).

Why a built-in instead of MCP:

- MCP servers add a protocol layer + subprocess and burn ~20K context tokens
  every call (Babich, "MCP is Dead", Apr 2026). For a small bandwidth-limited
  local model like Gemma 4 (131K context) that's a real cost.
- A direct built-in has tight, schema-enforced inputs (Pydantic), zero IPC,
  and cannot be hijacked by injected instructions the way a tool-rich MCP
  server can.
- The operations the model actually wants — factorial, prime test,
  statistics, exact fractions, large multiplies — are all stdlib and
  compose cleanly into a single safe expression evaluator.

The interface is deliberately ONE input: a Python-style expression evaluated
in a sandbox (math.*, statistics.*, Fraction, Decimal, basic operators,
no imports, no attribute access outside the whitelist). This keeps the tool
list small (one entry, not twenty) and lets the model express any computation
it might want without us having to enumerate every operation.

Examples the model can call:

    math(expression="2 + 3 * 4")
    math(expression="math.factorial(20)")
    math(expression="math.comb(50, 5)")
    math(expression="math.gcd(48, 18)")
    math(expression="statistics.mean([1, 2, 3, 4, 5])")
    math(expression="statistics.stdev([2, 4, 4, 4, 5, 5, 7, 9])")
    math(expression="Fraction(1, 3) + Fraction(1, 6)")
    math(expression="round(math.pi, 8)")
    math(expression="sum(range(1, 101))")     # 5050

Read-only, side-effect free, always auto-approved.
"""
from __future__ import annotations

import ast
import math
import statistics
from collections.abc import AsyncGenerator
from decimal import Decimal, getcontext
from fractions import Fraction
from typing import TYPE_CHECKING, Any, ClassVar, final

from pydantic import BaseModel, Field

from drydock.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolStreamEvent

if TYPE_CHECKING:
    from drydock.core.types import ToolResultEvent


# ── Safe expression evaluator ─────────────────────────────────────────
# Whitelist nodes + names so the model can't import os / open files /
# subprocess. Everything goes through `math.*`, `statistics.*`,
# `Fraction`, `Decimal` plus basic operators and a small builtins set.

_ALLOWED_NODES = {
    ast.Expression, ast.Constant,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.And, ast.Or, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Call, ast.Name, ast.Load, ast.Attribute,
    ast.Tuple, ast.List, ast.Subscript, ast.Slice, ast.Index,
}

_ALLOWED_NAMES: dict[str, Any] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
    "nan": math.nan,
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "len": len, "pow": pow, "divmod": divmod,
    "int": int, "float": float, "bool": bool, "complex": complex,
    "list": list, "tuple": tuple, "range": range,
    "math": math,
    "statistics": statistics,
    "Fraction": Fraction,
    "Decimal": Decimal,
}

_MAX_EXPR_LEN = 4000


def _validate_ast(node: ast.AST) -> None:
    for child in ast.walk(node):
        if type(child) not in _ALLOWED_NODES:
            raise ValueError(
                f"disallowed expression node: {type(child).__name__}"
            )
        if isinstance(child, ast.Attribute):
            if not (
                isinstance(child.value, ast.Name)
                and child.value.id in {"math", "statistics", "Fraction", "Decimal"}
            ):
                raise ValueError(
                    "attribute access only allowed on math, statistics, "
                    "Fraction, Decimal"
                )
        if isinstance(child, ast.Name) and child.id not in _ALLOWED_NAMES:
            raise ValueError(f"disallowed name: {child.id}")


def safe_eval(expression: str) -> Any:
    """Evaluate `expression` in a sandboxed environment.

    Raises ValueError on anything beyond arithmetic + math/statistics +
    Fraction/Decimal. Callers should catch and surface the error string
    rather than re-raising — see `MathTool.run`.
    """
    if len(expression) > _MAX_EXPR_LEN:
        raise ValueError(
            f"expression too long ({len(expression)} > {_MAX_EXPR_LEN})"
        )
    tree = ast.parse(expression, mode="eval")
    _validate_ast(tree)
    return eval(  # noqa: S307 — sandboxed via _validate_ast
        compile(tree, "<safe_eval>", "eval"),
        {"__builtins__": {}},
        _ALLOWED_NAMES,
    )


# ── Tool ───────────────────────────────────────────────────────────────


class MathArgs(BaseModel):
    expression: str = Field(
        description=(
            "A Python expression to evaluate. Whitelist: arithmetic operators "
            "(+ - * / // % **), comparisons, and/or/not, abs/round/min/max/"
            "sum/len/pow/divmod/range/int/float/bool, the math module "
            "(math.factorial, math.gcd, math.comb, math.sqrt, etc.), the "
            "statistics module (statistics.mean, statistics.stdev, ...), "
            "Fraction(num, den), Decimal('...'). Constants: pi, e, tau, "
            "inf, nan. NO imports, NO open(), NO file/network access, NO "
            "attribute access outside math/statistics/Fraction/Decimal."
        )
    )
    decimal_precision: int = Field(
        default=28,
        ge=1,
        le=200,
        description=(
            "Decimal context precision (digits). Only matters when the "
            "expression uses `Decimal(...)`. Default 28."
        ),
    )


class MathResult(BaseModel):
    ok: bool
    result: str = ""        # str so Fraction/Decimal/int-precision survive JSON
    result_type: str = ""   # "int" / "float" / "Fraction" / "Decimal" / "list" / etc.
    error: str = ""


class MathConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class Math(
    BaseTool[MathArgs, MathResult, MathConfig, BaseToolState],
    ToolUIData[MathArgs, MathResult],
):
    description: ClassVar[str] = (
        "Evaluate exact-math expressions via Python's stdlib (math, "
        "statistics, Fraction, Decimal). Use INSTEAD of doing arithmetic "
        "in your head when the answer needs to be exact: factorials "
        "above 12!, large multiplies, prime tests, modular arithmetic, "
        "GCD/LCM, statistics, exact fractions. One arg: `expression`. "
        "Examples: math.factorial(20), math.comb(50, 5), "
        "statistics.mean([1,2,3]), Fraction(1,3) + Fraction(1,6). "
        "Sandboxed — no imports, no file/network access."
    )

    @classmethod
    def format_call_display(cls, args: MathArgs) -> ToolCallDisplay:
        e = args.expression.strip()
        if len(e) > 60:
            e = e[:57] + "..."
        return ToolCallDisplay(summary=f"math: {e}")

    @classmethod
    def get_result_display(cls, event: "ToolResultEvent") -> ToolResultDisplay:
        if isinstance(event.result, MathResult):
            if not event.result.ok:
                return ToolResultDisplay(
                    success=False,
                    message=f"math: {event.result.error[:80]}",
                )
            preview = event.result.result
            if len(preview) > 80:
                preview = preview[:77] + "..."
            return ToolResultDisplay(success=True, message=f"= {preview}")
        return ToolResultDisplay(success=True, message="math complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Computing"

    def resolve_permission(self, args: MathArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: MathArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | MathResult, None]:
        if args.decimal_precision and args.decimal_precision != 28:
            getcontext().prec = args.decimal_precision
        try:
            value = safe_eval(args.expression)
        except SyntaxError as e:
            yield MathResult(
                ok=False, error=f"SyntaxError: {e.msg} at column {e.offset or '?'}"
            )
            return
        except ZeroDivisionError as e:
            yield MathResult(ok=False, error=f"ZeroDivisionError: {e}")
            return
        except (ValueError, OverflowError, ArithmeticError, TypeError) as e:
            yield MathResult(ok=False, error=f"{type(e).__name__}: {e}")
            return

        # Coerce non-JSON-friendly values to strings without losing exactness.
        if isinstance(value, (Fraction, Decimal)):
            yield MathResult(
                ok=True, result=str(value), result_type=type(value).__name__
            )
            return
        # Plain numbers / lists / tuples / bools — repr keeps int precision
        # for huge factorials etc.
        yield MathResult(
            ok=True,
            result=repr(value) if not isinstance(value, str) else value,
            result_type=type(value).__name__,
        )

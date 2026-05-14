"""Solve tool — constraint solving via Z3 (SMT solver).

The model offloads "find values satisfying these constraints" to Z3.
Strictly more capable than vanilla Prolog for the constraint-heavy
HLE problems we're targeting: integer/real arithmetic, modular
arithmetic, Boolean logic, bitvectors, all from one solver.

Operations (`op=`):

  solve(variables, constraints)
      Find ONE assignment satisfying all constraints. Returns "sat"
      with the model, or "unsat" / "unknown".

  prove(variables, constraints, conclusion)
      Prove `conclusion` follows from `constraints`. Internally checks
      that `constraints ∧ ¬conclusion` is unsat. Returns "valid" or
      a counter-example.

  find_all(variables, constraints, limit=10)
      Enumerate up to `limit` distinct assignments. Stops at limit or
      when no more solutions exist.

  optimize(variables, constraints, objective, direction)
      Find the assignment that minimizes (or maximizes) `objective`
      subject to `constraints`. `direction` ∈ {"min","max"}.

Variable declaration syntax:
    Comma-separated `name:Type` entries.
    Types: Int, Real, Bool, BitVec<N> (e.g. BitVec8).
    Example: "x:Int, y:Int, ok:Bool, byte:BitVec8"

Constraint syntax:
    Python expressions over the declared variables using Z3 operators:
        +  -  *  /  %       arithmetic
        == != <  <= >  >=   comparisons
        And(...), Or(...), Not(...), Implies(p, q), Xor(p, q)
        If(cond, a, b)      conditional value
        Distinct(a, b, ...) all-different
        Abs(x)              absolute value
        Sum([...])          sum over a list

Free names are auto-bound to the declared variables. Constraint
strings are AST-validated (no attribute access, no calls outside the
whitelist) before being eval'd in a restricted namespace.

Read-only, side-effect free, ALWAYS permission.
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


SolveOp = Literal["solve", "prove", "find_all", "optimize"]


_OP_HELP = (
    "Operation: solve (find one assignment) | prove (verify a "
    "conclusion follows from constraints) | find_all (enumerate up to "
    "`limit` solutions) | optimize (min/max an objective)."
)


class SolveArgs(BaseModel):
    op: SolveOp = Field(description=_OP_HELP)
    variables: str = Field(
        description=(
            "Comma-separated typed variable declarations. "
            "Types: Int, Real, Bool, BitVec<N>. "
            "Example: 'x:Int, y:Int, ok:Bool'."
        ),
    )
    constraints: list[str] = Field(
        default_factory=list,
        description=(
            "List of Python expression strings over the declared "
            "variables. Operators: +, -, *, /, %, ==, !=, <, <=, >, "
            ">=. Functions: And, Or, Not, Implies, Xor, If, Distinct, "
            "Abs, Sum."
        ),
    )
    conclusion: str = Field(
        default="",
        description=(
            "For `prove`: the proposition that should follow from "
            "constraints. Ignored by other ops."
        ),
    )
    objective: str = Field(
        default="",
        description=(
            "For `optimize`: the expression to minimize or maximize. "
            "Ignored by other ops."
        ),
    )
    direction: Literal["min", "max"] = Field(
        default="min",
        description="For `optimize`: 'min' or 'max'. Default: min.",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="For `find_all`: max number of distinct solutions to enumerate.",
    )
    timeout_ms: int = Field(
        default=5000,
        ge=100,
        le=30000,
        description="Solver timeout in milliseconds. Default 5000.",
    )


class SolveResult(BaseModel):
    ok: bool
    op: str = ""
    status: str = ""        # sat / unsat / unknown / valid / countered / optimal / infeasible
    model: str = ""         # one-line variable assignment, or empty
    models: list[str] = Field(default_factory=list)  # for find_all
    objective_value: str = ""  # for optimize
    error: str = ""


class SolveConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


# ── Sandboxed expression parser ──────────────────────────────────────────

_BAD_PATTERNS = (
    "__", "import", "exec", "eval", "open",
    "globals", "locals", "getattr", "setattr",
    "compile", "input",
)


def _z3_namespace(declared: dict[str, Any]) -> dict[str, Any]:
    """Build the eval namespace for parsing a constraint string.

    Includes the user-declared variables plus the whitelisted Z3
    functions. No __builtins__, no attribute access permitted.
    """
    import z3
    ns: dict[str, Any] = {
        "__builtins__": {},
        "And": z3.And, "Or": z3.Or, "Not": z3.Not,
        "Implies": z3.Implies, "Xor": z3.Xor,
        "If": z3.If, "Distinct": z3.Distinct,
        "Abs": z3.Abs, "Sum": z3.Sum,
        "True": True, "False": False,
    }
    ns.update(declared)
    return ns


def _validate_text(s: str, kind: str) -> None:
    if not s or not s.strip():
        raise ToolError(f"{kind} is empty")
    if len(s) > 4000:
        raise ToolError(f"{kind} too long (>4000 chars)")
    lower = s.lower()
    for bad in _BAD_PATTERNS:
        if bad in lower:
            raise ToolError(f"forbidden token in {kind}: {bad!r}")


def _declare_variables(spec: str) -> dict[str, Any]:
    """Parse `x:Int, y:Real, ok:Bool` into a name → z3-var dict."""
    import z3
    _validate_text(spec, "variables")
    out: dict[str, Any] = {}
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        if ":" not in part:
            raise ToolError(
                f"bad variable decl {part!r} — expected 'name:Type'"
            )
        name, ty = part.split(":", 1)
        name = name.strip()
        ty = ty.strip()
        if not name.isidentifier():
            raise ToolError(f"bad variable name {name!r}")
        if name in out:
            raise ToolError(f"duplicate variable name {name!r}")
        if ty == "Int":
            out[name] = z3.Int(name)
        elif ty == "Real":
            out[name] = z3.Real(name)
        elif ty == "Bool":
            out[name] = z3.Bool(name)
        elif ty.startswith("BitVec"):
            try:
                width = int(ty[len("BitVec"):])
            except ValueError as e:
                raise ToolError(f"bad BitVec width in {ty!r}") from e
            if not (1 <= width <= 64):
                raise ToolError(f"BitVec width must be 1..64, got {width}")
            out[name] = z3.BitVec(name, width)
        else:
            raise ToolError(
                f"unknown type {ty!r} (allowed: Int, Real, Bool, BitVec<N>)"
            )
    if not out:
        raise ToolError("no variables declared")
    if len(out) > 64:
        raise ToolError("too many variables (>64)")
    return out


def _parse_expr(expr: str, declared: dict[str, Any]):
    """AST-validate and evaluate one expression string."""
    _validate_text(expr, "expression")
    try:
        tree = _ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ToolError(f"SyntaxError: {e.msg} at column {e.offset or '?'}") from e

    ns = _z3_namespace(declared)
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Attribute):
            raise ToolError("attribute access not permitted in expressions")
        if isinstance(node, _ast.Call):
            fn = getattr(node.func, "id", None)
            if fn is None or fn not in ns:
                raise ToolError(
                    f"unknown function {fn!r} (allowed: "
                    f"{sorted(k for k in ns if k != '__builtins__' and not k.startswith('_'))})"
                )
        if isinstance(node, _ast.Name) and node.id not in ns:
            raise ToolError(
                f"undeclared name {node.id!r} — declare it via the "
                f"`variables` arg or it must be one of the whitelisted "
                f"functions"
            )

    try:
        return eval(  # noqa: S307 — whitelisted namespace, AST-validated
            compile(tree, "<solve>", "eval"), ns, {}
        )
    except (SyntaxError, ValueError, TypeError) as e:
        raise ToolError(f"{type(e).__name__}: {e}") from e


def _format_model(model, declared: dict[str, Any]) -> str:
    """Render z3 model as `name=value, name=value` for stable text output."""
    parts: list[str] = []
    for name, var in declared.items():
        v = model.eval(var, model_completion=True)
        parts.append(f"{name}={v}")
    return ", ".join(parts)


# ── Op implementations ───────────────────────────────────────────────────

def _op_solve(args: SolveArgs) -> SolveResult:
    import z3
    declared = _declare_variables(args.variables)
    if not args.constraints:
        raise ToolError("solve needs at least one constraint")
    s = z3.Solver()
    s.set("timeout", args.timeout_ms)
    for c in args.constraints:
        s.add(_parse_expr(c, declared))
    r = s.check()
    if r == z3.sat:
        return SolveResult(
            ok=True, op="solve", status="sat",
            model=_format_model(s.model(), declared),
        )
    if r == z3.unsat:
        return SolveResult(ok=True, op="solve", status="unsat")
    return SolveResult(ok=True, op="solve", status="unknown")


def _op_prove(args: SolveArgs) -> SolveResult:
    import z3
    declared = _declare_variables(args.variables)
    if not args.conclusion.strip():
        raise ToolError("prove needs a `conclusion` argument")
    s = z3.Solver()
    s.set("timeout", args.timeout_ms)
    for c in args.constraints:
        s.add(_parse_expr(c, declared))
    s.add(z3.Not(_parse_expr(args.conclusion, declared)))
    r = s.check()
    if r == z3.unsat:
        return SolveResult(ok=True, op="prove", status="valid")
    if r == z3.sat:
        return SolveResult(
            ok=True, op="prove", status="countered",
            model=_format_model(s.model(), declared),
        )
    return SolveResult(ok=True, op="prove", status="unknown")


def _op_find_all(args: SolveArgs) -> SolveResult:
    import z3
    declared = _declare_variables(args.variables)
    if not args.constraints:
        raise ToolError("find_all needs at least one constraint")
    s = z3.Solver()
    s.set("timeout", args.timeout_ms)
    for c in args.constraints:
        s.add(_parse_expr(c, declared))
    models: list[str] = []
    while len(models) < args.limit:
        r = s.check()
        if r != z3.sat:
            break
        m = s.model()
        models.append(_format_model(m, declared))
        # Block this exact assignment so the next iteration yields a different one.
        block = z3.Or(*[var != m.eval(var, model_completion=True)
                        for var in declared.values()])
        s.add(block)
    status = "sat" if models else "unsat"
    return SolveResult(
        ok=True, op="find_all", status=status, models=models,
        model=models[0] if models else "",
    )


def _op_optimize(args: SolveArgs) -> SolveResult:
    import z3
    declared = _declare_variables(args.variables)
    if not args.objective.strip():
        raise ToolError("optimize needs an `objective` argument")
    opt = z3.Optimize()
    opt.set("timeout", args.timeout_ms)
    for c in args.constraints:
        opt.add(_parse_expr(c, declared))
    obj_expr = _parse_expr(args.objective, declared)
    handle = opt.minimize(obj_expr) if args.direction == "min" else opt.maximize(obj_expr)
    r = opt.check()
    if r == z3.unsat:
        return SolveResult(ok=True, op="optimize", status="infeasible")
    if r != z3.sat:
        return SolveResult(ok=True, op="optimize", status="unknown")
    m = opt.model()
    return SolveResult(
        ok=True, op="optimize", status="optimal",
        model=_format_model(m, declared),
        objective_value=str(handle.value()),
    )


_DISPATCH = {
    "solve":    _op_solve,
    "prove":    _op_prove,
    "find_all": _op_find_all,
    "optimize": _op_optimize,
}


class Solve(
    BaseTool[SolveArgs, SolveResult, SolveConfig, BaseToolState],
    ToolUIData[SolveArgs, SolveResult],
):
    description: ClassVar[str] = (
        "Constraint solver via Z3 (SMT) — when you need to find values "
        "satisfying a set of constraints, prove a theorem follows from "
        "premises, enumerate all solutions, or optimize an objective. "
        "Operations: solve, prove, find_all, optimize. Use INSTEAD of "
        "working it out in your head — small models miscount, miss "
        "edge cases, and fumble modular arithmetic. Declare variables "
        "with `name:Type` (Int, Real, Bool, BitVec<N>). Constraints "
        "are Python expressions using ==, !=, <, <=, >, >=, +, -, *, "
        "/, %, And, Or, Not, Implies, Distinct, If, Abs, Sum. "
        "Examples: Einstein puzzle (Distinct + neighbor constraints), "
        "Sudoku (Distinct on rows/cols/boxes), 'find x s.t. 3x ≡ 5 "
        "(mod 7)', linear systems, optimization."
    )

    @classmethod
    def format_call_display(cls, args: SolveArgs) -> ToolCallDisplay:
        n = len(args.constraints)
        return ToolCallDisplay(
            summary=f"solve[{args.op}]: {args.variables[:30]} ({n} constraints)"
        )

    @classmethod
    def get_result_display(cls, event: "ToolResultEvent") -> ToolResultDisplay:
        if isinstance(event.result, SolveResult):
            if not event.result.ok:
                return ToolResultDisplay(
                    success=False, message=f"solve: {event.result.error[:80]}"
                )
            preview = event.result.model or event.result.status
            if event.result.op == "find_all" and event.result.models:
                preview = f"{len(event.result.models)} solutions; first: {event.result.models[0]}"
            if event.result.op == "optimize" and event.result.objective_value:
                preview = f"{event.result.objective_value} @ ({event.result.model})"
            if len(preview) > 120:
                preview = preview[:117] + "..."
            return ToolResultDisplay(success=True, message=f"{event.result.status}: {preview}")
        return ToolResultDisplay(success=True, message="solve complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Solving constraints"

    def resolve_permission(self, args: SolveArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: SolveArgs, ctx: "InvokeContext | None" = None
    ) -> AsyncGenerator["ToolStreamEvent | SolveResult", None]:
        handler = _DISPATCH.get(args.op)
        if handler is None:
            yield SolveResult(
                ok=False, op=args.op,
                error=f"unknown op {args.op!r}. Allowed: {list(_DISPATCH)}",
            )
            return
        try:
            yield handler(args)
        except ToolError as e:
            yield SolveResult(ok=False, op=args.op, error=str(e))
        except Exception as e:  # noqa: BLE001 — surface to model, don't crash
            yield SolveResult(
                ok=False, op=args.op, error=f"{type(e).__name__}: {e}"
            )

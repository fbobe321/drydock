"""Logic tool — symbolic propositional logic via sympy.

Companion to `math_tool`: where `math` offloads arithmetic the model
shouldn't trust itself with, `logic` offloads symbolic logic. Same
shape as `math` (direct built-in, sandboxed, schema-enforced inputs).

Why a built-in instead of asking the model to "reason about it":
Gemma 4 makes systematic errors on:
- Contrapositive (gets the direction of negation wrong)
- De Morgan over a chain of three-or-more conjuncts
- Quantifier negation (∀x ¬P vs ¬∀x P)
- Implication-to-disjunction rewrite (p → q ≡ ¬p ∨ q)

These ARE solved problems in sympy.logic.boolalg, which is in the
existing dependency tree. Wrapping it as a tool gives the model the
same reliability gain the math tool gives for arithmetic.

Operations (`op=`):

  evaluate(expression, vars)   — truth-evaluate at a variable assignment
  truth_table(expression)      — full truth table as text
  equivalent(expr1, expr2)     — semantic equivalence check
  tautology(expression)        — is it always True?
  contradiction(expression)    — is it always False?
  satisfiable(expression)      — is there ANY assignment that makes it True?
  cnf(expression)              — conjunctive normal form
  dnf(expression)              — disjunctive normal form
  simplify(expression)         — simplified form (sympy's simplify_logic)
  negate(expression)           — push ¬ inside via De Morgan
  contrapositive(expression)   — for p→q, return ¬q→¬p (parses Implies)
  modus_ponens(p, p_implies_q) — given p and (p→q), conclude q (sanity check)

Expression syntax: Python-style — `p & q | ~r >> s` where:
  &   AND
  |   OR
  ~   NOT
  >>  IMPLIES (p >> q means p → q)
  Equivalent(p, q)  for p ↔ q   (using the sympy spelling)

Free variables are auto-detected as sympy Symbols.

Read-only, side-effect free, ALWAYS permission.
"""
from __future__ import annotations

import json
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


LogicOp = Literal[
    "evaluate",
    "truth_table",
    "equivalent",
    "tautology",
    "contradiction",
    "satisfiable",
    "cnf",
    "dnf",
    "simplify",
    "negate",
    "contrapositive",
    "modus_ponens",
]


_OP_HELP = (
    "Operation: evaluate | truth_table | equivalent | tautology | "
    "contradiction | satisfiable | cnf | dnf | simplify | negate | "
    "contrapositive | modus_ponens. See the tool docstring for the "
    "shape each one expects."
)


class LogicArgs(BaseModel):
    op: LogicOp = Field(description=_OP_HELP)
    expression: str = Field(
        default="",
        description=(
            "Primary propositional expression. Python-style: `p & q | ~r >> s` "
            "(AND=&, OR=|, NOT=~, IMPLIES=>>, ↔=Equivalent(a,b)). Free "
            "variables (p, q, ...) are auto-bound as sympy symbols. "
            "Required for all ops except modus_ponens (which uses both)."
        ),
    )
    expression2: str = Field(
        default="",
        description=(
            "Second expression. Required for `equivalent` (compare against "
            "expression) and `modus_ponens` (the p→q premise — the first "
            "expression should be just `p`)."
        ),
    )
    variables: str = Field(
        default="",
        description=(
            "Comma-separated `name=truth` assignments for `evaluate`. "
            "Example: 'p=T, q=F'. Ignored by other ops."
        ),
    )


class LogicResult(BaseModel):
    ok: bool
    op: str = ""
    result: str = ""
    result_type: str = ""
    error: str = ""


class LogicConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


# ── Expression parser ─────────────────────────────────────────────────────

# Token-level sanitization: only allow [A-Za-z0-9_], operators, parens,
# whitespace, and the sympy function names we whitelist. Forbids dotted
# attribute access and dunder-anything.
_ALLOWED_TOKEN_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[\s&|~()<>!=,]+)$"
)
_BAD_PATTERNS = (
    "__", ".", "import", "exec", "eval", "open",
    "globals", "locals", "getattr", "setattr",
)
_ALLOWED_NAMES: dict[str, Any] = {}  # populated on first use


def _safe_globals() -> dict[str, Any]:
    """Lazy-build the eval namespace for parsing expressions. Restricted
    to sympy logic primitives + names the user can introduce as symbols."""
    global _ALLOWED_NAMES
    if _ALLOWED_NAMES:
        return _ALLOWED_NAMES
    from sympy import Symbol, true, false
    from sympy.logic.boolalg import (
        And, Or, Not, Implies, Equivalent, Xor, ITE, Nand, Nor,
    )
    _ALLOWED_NAMES = {
        "__builtins__": {},
        # Sympy logic primitives — the model can call these directly when
        # it wants explicit AST shape.
        "And": And, "Or": Or, "Not": Not, "Implies": Implies,
        "Equivalent": Equivalent, "Xor": Xor, "ITE": ITE,
        "Nand": Nand, "Nor": Nor,
        "T": true, "F": false, "True": true, "False": false,
    }
    return _ALLOWED_NAMES


def _validate_expression(expr: str) -> None:
    if not expr or not expr.strip():
        raise ToolError("expression is empty")
    if len(expr) > 4000:
        raise ToolError("expression too long (>4000 chars)")
    lower = expr.lower()
    for bad in _BAD_PATTERNS:
        if bad in lower:
            raise ToolError(f"forbidden token in expression: {bad!r}")


def _free_symbols(expr) -> list:
    """Sorted list of free Symbol objects, by name."""
    return sorted(expr.free_symbols, key=lambda s: s.name)


def _parse(expr: str):
    """Compile and evaluate `expr` as a sympy Boolean expression.

    Auto-creates Symbol objects for any name not in the whitelist —
    that's how the user writes `p & q` and gets back `And(p, q)`.
    """
    _validate_expression(expr)

    import ast as _ast
    from sympy import Symbol

    # First parse with Python's ast to ensure it's valid Python.
    try:
        tree = _ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ToolError(f"SyntaxError: {e.msg} at column {e.offset or '?'}") from e

    # Walk to find Name nodes — those become Symbols.
    safe_globals = dict(_safe_globals())
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Attribute):
            raise ToolError("attribute access not permitted")
        if isinstance(node, _ast.Call):
            fn = getattr(node.func, "id", None)
            if fn is None or fn not in safe_globals:
                raise ToolError(
                    f"unknown function {fn!r} (allowed: "
                    f"{sorted(k for k in safe_globals if k != '__builtins__')})"
                )
        if isinstance(node, _ast.Name):
            n = node.id
            if n not in safe_globals:
                safe_globals[n] = Symbol(n)

    try:
        return eval(  # noqa: S307 — whitelisted namespace, AST-validated
            compile(tree, "<logic>", "eval"), safe_globals, {}
        )
    except (SyntaxError, ValueError, TypeError) as e:
        raise ToolError(f"{type(e).__name__}: {e}") from e


# ── Op implementations ───────────────────────────────────────────────────

def _op_evaluate(expr_str: str, variables: str) -> tuple[str, str]:
    from sympy import true, false
    expr = _parse(expr_str)
    if not variables.strip():
        # No assignment — only acceptable if expr has no free symbols.
        if _free_symbols(expr):
            raise ToolError(
                "evaluate needs a `variables=` assignment when the "
                "expression has free symbols: "
                f"{[s.name for s in _free_symbols(expr)]}"
            )
        return ("true" if bool(expr) else "false", "bool")

    # Parse `p=T, q=F` into a dict
    assignment: dict = {}
    for part in variables.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ToolError(
                f"bad variables entry {part!r} — expected `name=T` or `name=F`"
            )
        name, val = part.split("=", 1)
        name = name.strip()
        v = val.strip().lower()
        if v in ("t", "true", "1"):
            assignment[name] = true
        elif v in ("f", "false", "0"):
            assignment[name] = false
        else:
            raise ToolError(f"bad truth value {val!r} for {name!r}")

    # Substitute by name
    from sympy import Symbol
    sub_map = {Symbol(k): v for k, v in assignment.items()}
    result = expr.subs(sub_map)
    if result is true or result is True:
        return ("true", "bool")
    if result is false or result is False:
        return ("false", "bool")
    return (str(result), "Boolean")


def _op_truth_table(expr_str: str) -> tuple[str, str]:
    from sympy import true, false
    from itertools import product
    expr = _parse(expr_str)
    syms = _free_symbols(expr)
    if not syms:
        return ("true" if bool(expr) else "false", "constant")
    if len(syms) > 6:
        raise ToolError(
            f"truth_table refuses {len(syms)} variables "
            "(2^N rows; cap is 6). Pick a sub-expression."
        )
    header = " ".join(s.name for s in syms) + " | result"
    lines = [header, "-" * len(header)]
    for combo in product((false, true), repeat=len(syms)):
        sub_map = {sym: val for sym, val in zip(syms, combo)}
        val = expr.subs(sub_map)
        row_vals = " ".join("T" if c is true else "F" for c in combo)
        out = "T" if val is true or val is True else (
            "F" if val is false or val is False else str(val)
        )
        lines.append(f"{row_vals} | {out}")
    return ("\n".join(lines), "truth_table")


def _op_equivalent(expr1_str: str, expr2_str: str) -> tuple[str, str]:
    if not expr2_str:
        raise ToolError("equivalent needs `expression2`")
    from sympy.logic.boolalg import Equivalent
    from sympy import simplify_logic
    e1 = _parse(expr1_str)
    e2 = _parse(expr2_str)
    # Equivalent(a,b) is a tautology iff a ≡ b.
    same = simplify_logic(Equivalent(e1, e2)) is __import__("sympy").true
    return (("true" if same else "false") + f"  ({e1} {'≡' if same else '≢'} {e2})", "bool")


def _op_tautology(expr_str: str) -> tuple[str, str]:
    from sympy import simplify_logic, true
    e = _parse(expr_str)
    return ("true" if simplify_logic(e) is true else "false", "bool")


def _op_contradiction(expr_str: str) -> tuple[str, str]:
    from sympy import simplify_logic, false
    e = _parse(expr_str)
    return ("true" if simplify_logic(e) is false else "false", "bool")


def _op_satisfiable(expr_str: str) -> tuple[str, str]:
    from sympy.logic.inference import satisfiable
    e = _parse(expr_str)
    sat = satisfiable(e)
    if not sat:
        return ("false  (no assignment makes this true — contradiction)", "bool")
    # sat is either True (tautology) or a dict of one satisfying assignment.
    if sat is True:
        return ("true  (tautology — any assignment satisfies)", "bool")
    pieces = ", ".join(f"{k.name}={'T' if v else 'F'}" for k, v in sat.items())
    return (f"true  (one witness: {pieces})", "bool")


def _op_cnf(expr_str: str) -> tuple[str, str]:
    from sympy.logic.boolalg import to_cnf
    return (str(to_cnf(_parse(expr_str), simplify=True)), "expr")


def _op_dnf(expr_str: str) -> tuple[str, str]:
    from sympy.logic.boolalg import to_dnf
    return (str(to_dnf(_parse(expr_str), simplify=True)), "expr")


def _op_simplify(expr_str: str) -> tuple[str, str]:
    from sympy import simplify_logic
    return (str(simplify_logic(_parse(expr_str))), "expr")


def _op_negate(expr_str: str) -> tuple[str, str]:
    from sympy import simplify_logic
    from sympy.logic.boolalg import Not
    e = _parse(expr_str)
    return (str(simplify_logic(Not(e))), "expr")


def _op_contrapositive(expr_str: str) -> tuple[str, str]:
    """Return the contrapositive of `p → q` (i.e. `¬q → ¬p`).

    Accepts either Python-shape `p >> q`, the sympy spelling
    `Implies(p, q)`, or the disjunctive equivalent `~p | q` (which is
    logically the same as `Implies(p, q)`).
    """
    from sympy.logic.boolalg import Implies, Not, Or
    e = _parse(expr_str)
    if isinstance(e, Implies):
        p, q = e.args
        return (str(Implies(Not(q), Not(p))), "expr")
    # Sympy canonicalises `Implies(p, q)` to `q | ~p`. Recover the
    # implication shape: if `e` is an Or with exactly two args where
    # one is `Not(X)`, treat as `Implies(X, the_other_arg)`.
    if isinstance(e, Or) and len(e.args) == 2:
        a, b = e.args
        if isinstance(a, Not):
            return (str(Implies(Not(b), Not(a.args[0]))), "expr")
        if isinstance(b, Not):
            return (str(Implies(Not(a), Not(b.args[0]))), "expr")
    raise ToolError(
        f"contrapositive needs an implication shape (e.g. `p >> q` or "
        f"`Implies(p, q)`). Got: {e}"
    )


def _op_modus_ponens(p_str: str, pq_str: str) -> tuple[str, str]:
    """Given `p` and `p → q`, return `q`. Verifies the antecedent of the
    second expression matches the first; otherwise raises."""
    from sympy.logic.boolalg import Implies
    p = _parse(p_str)
    pq = _parse(pq_str)
    if not isinstance(pq, Implies):
        raise ToolError(
            f"expression2 must be an implication for modus_ponens. Got: {pq}"
        )
    ant, con = pq.args
    if ant != p:
        raise ToolError(
            f"modus_ponens antecedent {ant!r} doesn't match given p {p!r}"
        )
    return (str(con), "expr")


_DISPATCH = {
    "evaluate":      lambda a: _op_evaluate(a.expression, a.variables),
    "truth_table":   lambda a: _op_truth_table(a.expression),
    "equivalent":    lambda a: _op_equivalent(a.expression, a.expression2),
    "tautology":     lambda a: _op_tautology(a.expression),
    "contradiction": lambda a: _op_contradiction(a.expression),
    "satisfiable":   lambda a: _op_satisfiable(a.expression),
    "cnf":           lambda a: _op_cnf(a.expression),
    "dnf":           lambda a: _op_dnf(a.expression),
    "simplify":      lambda a: _op_simplify(a.expression),
    "negate":        lambda a: _op_negate(a.expression),
    "contrapositive": lambda a: _op_contrapositive(a.expression),
    "modus_ponens":  lambda a: _op_modus_ponens(a.expression, a.expression2),
}


class Logic(
    BaseTool[LogicArgs, LogicResult, LogicConfig, BaseToolState],
    ToolUIData[LogicArgs, LogicResult],
):
    description: ClassVar[str] = (
        "Symbolic propositional logic via sympy — when you need to apply "
        "contrapositive, De Morgan, check if two expressions are "
        "equivalent, generate a truth table, or sanity-check a Modus "
        "Ponens step. Operations: evaluate, truth_table, equivalent, "
        "tautology, contradiction, satisfiable, cnf, dnf, simplify, "
        "negate, contrapositive, modus_ponens. Use INSTEAD of reasoning "
        "in your head — small models get implication direction wrong "
        "and miss negations under nested AND/OR. Expression syntax: "
        "`p & q | ~r >> s` (AND=&, OR=|, NOT=~, IMPLIES=>>, IFF=Equivalent(a,b))."
    )

    @classmethod
    def format_call_display(cls, args: LogicArgs) -> ToolCallDisplay:
        e = args.expression.strip()
        if len(e) > 40:
            e = e[:37] + "..."
        return ToolCallDisplay(summary=f"logic[{args.op}]: {e}")

    @classmethod
    def get_result_display(cls, event: "ToolResultEvent") -> ToolResultDisplay:
        if isinstance(event.result, LogicResult):
            if not event.result.ok:
                return ToolResultDisplay(
                    success=False, message=f"logic: {event.result.error[:80]}"
                )
            preview = event.result.result
            # First line for truth tables
            preview = preview.split("\n", 1)[0]
            if len(preview) > 80:
                preview = preview[:77] + "..."
            return ToolResultDisplay(success=True, message=f"= {preview}")
        return ToolResultDisplay(success=True, message="logic complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Reasoning logically"

    def resolve_permission(self, args: LogicArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: LogicArgs, ctx: "InvokeContext | None" = None
    ) -> AsyncGenerator["ToolStreamEvent | LogicResult", None]:
        handler = _DISPATCH.get(args.op)
        if handler is None:
            yield LogicResult(
                ok=False, op=args.op,
                error=f"unknown op {args.op!r}. Allowed: {list(_DISPATCH)}",
            )
            return
        try:
            value, rtype = handler(args)
        except ToolError as e:
            yield LogicResult(ok=False, op=args.op, error=str(e))
            return
        except Exception as e:  # noqa: BLE001 — surface to model, don't crash
            yield LogicResult(
                ok=False, op=args.op, error=f"{type(e).__name__}: {e}"
            )
            return
        yield LogicResult(ok=True, op=args.op, result=value, result_type=rtype)

"""Units tool — physics unit conversion and dimensional analysis.

Seventh tool in the symbolic-math stack. HLE Physics is 0/12 lifetime;
unit reasoning is the most common skill needed. sympy.physics.units
handles SI/imperial conversion, dimensional analysis, and unit-bearing
arithmetic correctly — the model frequently doesn't.

Backend: sympy.physics.units.

Operations (`op=`):

  convert(value, from, to)  — convert `value from_unit` → `to_unit`
                              (e.g. "60 miles per hour" → "m/s")
  evaluate(expression)      — compute `expression` and return with simplified units
  dimension(expression)     — return the SI dimension (L^a M^b T^c ...)
  to_si(expression)         — convert to base SI units
  consistent(expr1, expr2)  — do the two expressions share the same dimension?
  list_units(category)      — list known units of category (length/mass/time/...)

Expression syntax: Python-style with the sympy.physics.units namespace
auto-bound. Examples:
  expression="5 * meter / second"
  expression="0.5 * kg * (3 * m / s)**2"
  expression="9.81 * m / s**2 * 1 * kg"

Common unit names: meter, kilometer, mile, foot, inch; second, minute,
hour, day; gram, kilogram, pound; newton, joule, watt; coulomb, volt,
ampere, ohm; pascal, atmosphere; mole, kelvin.

Sandboxed: no imports, no attribute access, no dunder; 4000-char cap.
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


UnitsOp = Literal[
    "convert", "evaluate", "dimension", "to_si", "consistent", "list_units",
]


class UnitsArgs(BaseModel):
    op: UnitsOp = Field(
        description="Operation: convert | evaluate | dimension | to_si | consistent | list_units."
    )
    expression: str = Field(
        default="",
        description=(
            "Primary unit-bearing expression. Examples: '5*meter/second', "
            "'0.5*kg*(3*m/s)**2', '60*mile/hour'. Variables are bound to "
            "sympy.physics.units; common abbreviations work (m, s, kg, "
            "N, J, V, A, mole, K)."
        ),
    )
    expression2: str = Field(
        default="",
        description="Second expression for `consistent`.",
    )
    to_unit: str = Field(
        default="",
        description="Target unit for `convert` (e.g. 'm/s', 'joule', 'mile/hour').",
    )
    category: str = Field(
        default="",
        description=(
            "For list_units: one of length, time, mass, force, energy, "
            "power, charge, voltage, current, pressure, temperature, "
            "amount_of_substance."
        ),
    )


class UnitsResult(BaseModel):
    ok: bool
    op: str = ""
    result: str = ""
    result_type: str = ""
    error: str = ""


class UnitsConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


# ── Unit namespace + parser ──────────────────────────────────────────

_BAD_PATTERNS = (
    "__", "import", "exec", "eval", "open",
    "globals", "locals", "getattr", "setattr",
)
_UNIT_GLOBALS: dict[str, Any] = {}

_CATEGORY_UNITS: dict[str, list[str]] = {
    "length": ["meter", "m", "kilometer", "km", "centimeter", "cm", "millimeter",
               "mm", "mile", "foot", "ft", "inch", "in", "yard", "yd"],
    "time": ["second", "s", "minute", "hour", "h", "day"],
    "mass": ["gram", "g", "kilogram", "kg", "milligram", "mg", "pound", "lb",
             "ounce", "oz", "tonne"],
    "force": ["newton", "N", "dyne", "pound_force"],
    "energy": ["joule", "J", "kilojoule", "kJ", "calorie", "kilocalorie",
               "electronvolt", "eV"],
    "power": ["watt", "W", "kilowatt", "kW", "horsepower"],
    "charge": ["coulomb", "C"],
    "voltage": ["volt", "V"],
    "current": ["ampere", "A"],
    "pressure": ["pascal", "Pa", "atmosphere", "atm", "bar", "torr", "mmHg"],
    "temperature": ["kelvin", "K", "celsius"],
    "amount_of_substance": ["mole", "mol"],
}


def _unit_globals() -> dict[str, Any]:
    """Build the namespace mapping unit names to sympy Quantity objects.
    Includes both long form ('meter') and short form ('m')."""
    global _UNIT_GLOBALS
    if _UNIT_GLOBALS:
        return _UNIT_GLOBALS
    import sympy
    import sympy.physics.units as U
    g: dict[str, Any] = {
        "__builtins__": {},
        "pi": sympy.pi, "e": sympy.E, "Rational": sympy.Rational,
        "sqrt": sympy.sqrt, "Abs": sympy.Abs,
    }
    # Pull every name from sympy.physics.units (filters to Quantity objects).
    for name in dir(U):
        if name.startswith("_"):
            continue
        obj = getattr(U, name)
        # Quantity objects + the convert_to fn are what we want.
        if hasattr(obj, "name") or callable(obj):
            g[name] = obj
    # Common abbreviations and aliases
    aliases = {
        "m": "meter", "km": "kilometer", "cm": "centimeter", "mm": "millimeter",
        "s": "second", "h": "hour",
        "g": "gram", "kg": "kilogram", "mg": "milligram",
        "N": "newton", "J": "joule", "W": "watt", "C": "coulomb",
        "V": "volt", "A": "ampere", "Pa": "pascal", "K": "kelvin",
        "Hz": "hertz", "ft": "foot", "in": "inch", "yd": "yard",
        "lb": "pound", "oz": "ounce", "eV": "electronvolt",
        "kJ": "kilojoule", "kW": "kilowatt", "mol": "mole",
        "atm": "atmosphere",
    }
    for short, long in aliases.items():
        if long in g and short not in g:
            g[short] = g[long]
    _UNIT_GLOBALS = g
    return _UNIT_GLOBALS


def _parse(expr: str, *, name: str = "expression"):
    if not expr or not expr.strip():
        raise ToolError(f"{name} is empty")
    if len(expr) > 4000:
        raise ToolError(f"{name} too long")
    lower = expr.lower()
    for bad in _BAD_PATTERNS:
        if bad in lower:
            raise ToolError(f"forbidden token in {name}: {bad!r}")
    try:
        tree = _ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ToolError(f"{name} SyntaxError: {e.msg}") from e
    safe_g = _unit_globals()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Attribute):
            raise ToolError(f"{name}: attribute access not permitted")
        if isinstance(node, _ast.Call):
            fn = getattr(node.func, "id", None)
            if fn is None or fn not in safe_g:
                raise ToolError(f"{name}: unknown function {fn!r}")
        if isinstance(node, _ast.Name):
            if node.id not in safe_g:
                raise ToolError(f"{name}: unknown name {node.id!r}")
    try:
        return eval(  # noqa: S307 — whitelisted
            compile(tree, "<units>", "eval"), safe_g, {}
        )
    except (ValueError, TypeError) as e:
        raise ToolError(f"{name}: {type(e).__name__}: {e}") from e


# ── Op implementations ───────────────────────────────────────────────

def _op_convert(args: UnitsArgs) -> tuple[str, str]:
    import sympy.physics.units as U
    if not args.to_unit:
        raise ToolError("convert needs `to_unit=`")
    expr = _parse(args.expression)
    target = _parse(args.to_unit, name="to_unit")
    converted = U.convert_to(expr, target)
    return (str(converted), "quantity")


def _op_evaluate(args: UnitsArgs) -> tuple[str, str]:
    import sympy
    return (str(sympy.simplify(_parse(args.expression))), "expr")


def _op_dimension(args: UnitsArgs) -> tuple[str, str]:
    import sympy.physics.units as U
    from sympy.physics.units.systems import SI
    dim = SI.get_dimensional_expr(_parse(args.expression))
    return (str(dim), "dimension")


def _op_to_si(args: UnitsArgs) -> tuple[str, str]:
    import sympy.physics.units as U
    # Convert to base SI units (m, kg, s, A, K, mol, cd).
    base = [U.meter, U.kilogram, U.second, U.ampere, U.kelvin, U.mole, U.candela]
    return (str(U.convert_to(_parse(args.expression), base)), "si_expr")


def _op_consistent(args: UnitsArgs) -> tuple[str, str]:
    import sympy
    from sympy.physics.units.systems import SI
    if not args.expression2:
        raise ToolError("consistent needs `expression2=`")
    d1 = SI.get_dimensional_expr(_parse(args.expression))
    d2 = SI.get_dimensional_expr(_parse(args.expression2, name="expression2"))
    # Reduce both to base SI dimensions so e.g. `energy` and `force*length`
    # compare equal. sympy.physics.units names compound dimensions (`energy`)
    # without auto-expanding to their base form (L²·M·T⁻²).
    dim_sys = SI.get_dimension_system()
    deps1 = dim_sys.get_dimensional_dependencies(d1)
    deps2 = dim_sys.get_dimensional_dependencies(d2)
    same = deps1 == deps2
    return (
        f"{same}  (dim1={d1}, dim2={d2}, base1={dict(deps1)}, base2={dict(deps2)})",
        "bool",
    )


def _op_list_units(args: UnitsArgs) -> tuple[str, str]:
    cat = args.category.strip().lower()
    if not cat:
        return (", ".join(sorted(_CATEGORY_UNITS)), "list")
    if cat not in _CATEGORY_UNITS:
        raise ToolError(
            f"unknown category {cat!r}. Allowed: {sorted(_CATEGORY_UNITS)}"
        )
    return (", ".join(_CATEGORY_UNITS[cat]), "list")


_DISPATCH = {
    "convert":    _op_convert,
    "evaluate":   _op_evaluate,
    "dimension":  _op_dimension,
    "to_si":      _op_to_si,
    "consistent": _op_consistent,
    "list_units": _op_list_units,
}


class Units(
    BaseTool[UnitsArgs, UnitsResult, UnitsConfig, BaseToolState],
    ToolUIData[UnitsArgs, UnitsResult],
):
    description: ClassVar[str] = (
        "Physics unit conversion + dimensional analysis via "
        "sympy.physics.units. Convert between SI/imperial, simplify "
        "unit-bearing expressions, check whether two expressions share "
        "the same dimension, list available units per category. "
        "Use INSTEAD of remembering conversion factors and INSTEAD of "
        "doing unit algebra in your head. Common units: m/km/mile/ft, "
        "s/hour, kg/g/lb, N/J/W, V/A/C/Ω, atm/Pa, K, mol."
    )

    @classmethod
    def format_call_display(cls, args: UnitsArgs) -> ToolCallDisplay:
        e = args.expression.strip()
        if len(e) > 40:
            e = e[:37] + "..."
        return ToolCallDisplay(summary=f"units[{args.op}]: {e}")

    @classmethod
    def get_result_display(cls, event: "ToolResultEvent") -> ToolResultDisplay:
        if isinstance(event.result, UnitsResult):
            if not event.result.ok:
                return ToolResultDisplay(
                    success=False, message=f"units: {event.result.error[:80]}"
                )
            preview = event.result.result
            if len(preview) > 80:
                preview = preview[:77] + "..."
            return ToolResultDisplay(success=True, message=f"= {preview}")
        return ToolResultDisplay(success=True, message="units complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Computing"

    def resolve_permission(self, args: UnitsArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: UnitsArgs, ctx: "InvokeContext | None" = None
    ) -> AsyncGenerator["ToolStreamEvent | UnitsResult", None]:
        handler = _DISPATCH.get(args.op)
        if handler is None:
            yield UnitsResult(
                ok=False, op=args.op,
                error=f"unknown op {args.op!r}. Allowed: {list(_DISPATCH)}",
            )
            return
        try:
            value, rtype = handler(args)
        except ToolError as e:
            yield UnitsResult(ok=False, op=args.op, error=str(e))
            return
        except Exception as e:  # noqa: BLE001
            yield UnitsResult(
                ok=False, op=args.op, error=f"{type(e).__name__}: {e}"
            )
            return
        yield UnitsResult(ok=True, op=args.op, result=value, result_type=rtype)

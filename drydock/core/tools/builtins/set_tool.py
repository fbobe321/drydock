"""Set tool — discrete-math set operations.

Fourth addition to the symbolic-math stack (after logic / algebra /
number_theory). Handles set algebra that comes up in HLE Math:
unions, intersections, differences, power set, Cartesian product,
cardinality, subset / superset checks.

Backend: Python's frozenset + itertools.

Operations (`op=`):

  union(a, b)              — A ∪ B
  intersection(a, b)       — A ∩ B
  difference(a, b)         — A \\ B  (elements in A not in B)
  symmetric_difference(a, b) — A △ B = (A ∪ B) \\ (A ∩ B)
  is_subset(a, b)          — A ⊆ B?
  is_superset(a, b)        — A ⊇ B?
  is_disjoint(a, b)        — A ∩ B = ∅?
  cardinality(a)           — |A|
  power_set(a)             — 2^A (refuses |A| > 10 → would be 1024+ sets)
  product(a, b)            — A × B (Cartesian product, refuses if > 1000 pairs)
  size_of_product(a, b)    — |A × B| = |A| · |B|

Input syntax: comma-separated values in `a` and `b`. Each value can be
a Python int literal, a quoted string, or an unquoted bareword
(treated as a string token). Examples:

  a="1, 2, 3, 4"
  a="'apple', 'banana'"
  a="x, y, z"   (3 string elements: 'x', 'y', 'z')

Sandboxed: max 1000 elements per input set; no arbitrary code eval.
"""
from __future__ import annotations

import ast as _ast
from collections.abc import AsyncGenerator
from itertools import product as _iproduct
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


SetOp = Literal[
    "union", "intersection", "difference", "symmetric_difference",
    "is_subset", "is_superset", "is_disjoint",
    "cardinality", "power_set", "product", "size_of_product",
]


class SetArgs(BaseModel):
    op: SetOp = Field(
        description=(
            "Operation: union | intersection | difference | "
            "symmetric_difference | is_subset | is_superset | is_disjoint | "
            "cardinality | power_set | product | size_of_product."
        )
    )
    a: str = Field(
        default="",
        description=(
            "First set as a comma-separated list of elements. Each element "
            "is a Python int literal, a quoted string, or an unquoted "
            "bareword (treated as a string token). Examples: `1, 2, 3` or "
            "`'apple', 'banana'` or `x, y, z`."
        ),
    )
    b: str = Field(
        default="",
        description=(
            "Second set as a comma-separated list. Required for binary "
            "ops (union, intersection, difference, symmetric_difference, "
            "is_subset, is_superset, is_disjoint, product, size_of_product)."
        ),
    )


class SetResult(BaseModel):
    ok: bool
    op: str = ""
    result: str = ""
    result_type: str = ""
    error: str = ""


class SetConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


# ── Set-literal parser ────────────────────────────────────────────────

_MAX_SET_ELEMS = 1000


def _parse_element(token: str) -> Any:
    """Parse one element. Int literal, quoted str, or bareword (as str)."""
    token = token.strip()
    if not token:
        raise ToolError("empty element in set literal")
    # Try as a Python literal first (int, float, str, bool, None).
    try:
        return _ast.literal_eval(token)
    except (ValueError, SyntaxError):
        pass
    # Fall back: bareword identifier → treat as a string.
    if all(c.isalnum() or c == "_" for c in token):
        return token
    raise ToolError(
        f"can't parse set element {token!r}; use int, 'quoted', or bareword"
    )


def _parse_set(s: str, *, name: str = "set") -> frozenset:
    if not s.strip():
        raise ToolError(f"{name} is empty")
    if len(s) > 4000:
        raise ToolError(f"{name} too long (>4000 chars)")
    parts = []
    # Split on top-level commas only (we don't try to parse nested structures
    # — set elements are scalars here).
    depth = 0
    cur = []
    in_str: str | None = None
    for ch in s:
        if in_str:
            cur.append(ch)
            if ch == in_str:
                in_str = None
            continue
        if ch in ("'", '"'):
            in_str = ch
            cur.append(ch)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    elems = []
    for p in parts:
        if not p.strip():
            continue
        elems.append(_parse_element(p))
    if len(elems) > _MAX_SET_ELEMS:
        raise ToolError(
            f"{name}: {len(elems)} elements exceeds cap {_MAX_SET_ELEMS}"
        )
    return frozenset(elems)


def _fmt_set(s: frozenset) -> str:
    """Pretty-print a frozenset as `{a, b, c}` with stable ordering."""
    if not s:
        return "set()"
    # Sort heterogeneously: by (type_name, str) so ints first then strs etc.
    items = sorted(s, key=lambda v: (type(v).__name__, str(v)))
    parts = []
    for v in items:
        if isinstance(v, str):
            parts.append(repr(v))
        else:
            parts.append(repr(v))
    return "{" + ", ".join(parts) + "}"


# ── Op implementations ───────────────────────────────────────────────

def _op_union(a: SetArgs) -> tuple[str, str]:
    return (_fmt_set(_parse_set(a.a, name="a") | _parse_set(a.b, name="b")), "set")


def _op_intersection(a: SetArgs) -> tuple[str, str]:
    return (_fmt_set(_parse_set(a.a, name="a") & _parse_set(a.b, name="b")), "set")


def _op_difference(a: SetArgs) -> tuple[str, str]:
    return (_fmt_set(_parse_set(a.a, name="a") - _parse_set(a.b, name="b")), "set")


def _op_symmetric_difference(a: SetArgs) -> tuple[str, str]:
    return (_fmt_set(_parse_set(a.a, name="a") ^ _parse_set(a.b, name="b")), "set")


def _op_is_subset(a: SetArgs) -> tuple[str, str]:
    return (str(_parse_set(a.a, name="a") <= _parse_set(a.b, name="b")), "bool")


def _op_is_superset(a: SetArgs) -> tuple[str, str]:
    return (str(_parse_set(a.a, name="a") >= _parse_set(a.b, name="b")), "bool")


def _op_is_disjoint(a: SetArgs) -> tuple[str, str]:
    return (
        str(_parse_set(a.a, name="a").isdisjoint(_parse_set(a.b, name="b"))),
        "bool",
    )


def _op_cardinality(a: SetArgs) -> tuple[str, str]:
    return (str(len(_parse_set(a.a, name="a"))), "int")


def _op_power_set(a: SetArgs) -> tuple[str, str]:
    s = _parse_set(a.a, name="a")
    if len(s) > 10:
        raise ToolError(
            f"power_set refuses |A|={len(s)} (would emit {2**len(s)} subsets). "
            "Cap is 10."
        )
    from itertools import chain, combinations
    items = sorted(s, key=lambda v: (type(v).__name__, str(v)))
    all_subsets = chain.from_iterable(
        combinations(items, r) for r in range(len(items) + 1)
    )
    formatted = [_fmt_set(frozenset(ss)) for ss in all_subsets]
    return ("{" + ", ".join(formatted) + "}", "set_of_sets")


def _op_product(a: SetArgs) -> tuple[str, str]:
    sa = _parse_set(a.a, name="a")
    sb = _parse_set(a.b, name="b")
    if len(sa) * len(sb) > 1000:
        raise ToolError(
            f"product refuses |A|·|B| = {len(sa) * len(sb)} (cap 1000). "
            "Use size_of_product if you only need the count."
        )
    pairs = sorted(_iproduct(
        sorted(sa, key=lambda v: (type(v).__name__, str(v))),
        sorted(sb, key=lambda v: (type(v).__name__, str(v))),
    ))
    return (str(pairs), "list_of_pairs")


def _op_size_of_product(a: SetArgs) -> tuple[str, str]:
    sa = _parse_set(a.a, name="a")
    sb = _parse_set(a.b, name="b")
    return (str(len(sa) * len(sb)), "int")


_DISPATCH = {
    "union":                _op_union,
    "intersection":         _op_intersection,
    "difference":           _op_difference,
    "symmetric_difference": _op_symmetric_difference,
    "is_subset":            _op_is_subset,
    "is_superset":          _op_is_superset,
    "is_disjoint":          _op_is_disjoint,
    "cardinality":          _op_cardinality,
    "power_set":            _op_power_set,
    "product":              _op_product,
    "size_of_product":      _op_size_of_product,
}


class Set(
    BaseTool[SetArgs, SetResult, SetConfig, BaseToolState],
    ToolUIData[SetArgs, SetResult],
):
    description: ClassVar[str] = (
        "Discrete-math set operations — union, intersection, difference, "
        "symmetric difference, subset / superset / disjoint checks, "
        "cardinality, power set, Cartesian product. Use for HLE Math "
        "questions that ask 'how many elements of A satisfy ...' or "
        "'is A ⊆ B' or 'list the subsets of {1,2,3}'. Inputs are "
        "comma-separated literals (ints / 'quoted' / bareword). "
        "Sandboxed; element-count and product-size capped to keep "
        "output bounded."
    )

    @classmethod
    def format_call_display(cls, args: SetArgs) -> ToolCallDisplay:
        a_preview = args.a[:30] if args.a else ""
        b_preview = (", b=" + args.b[:30]) if args.b else ""
        return ToolCallDisplay(
            summary=f"set[{args.op}]: {a_preview}{b_preview}"[:60]
        )

    @classmethod
    def get_result_display(cls, event: "ToolResultEvent") -> ToolResultDisplay:
        if isinstance(event.result, SetResult):
            if not event.result.ok:
                return ToolResultDisplay(
                    success=False, message=f"set: {event.result.error[:80]}"
                )
            preview = event.result.result
            if len(preview) > 80:
                preview = preview[:77] + "..."
            return ToolResultDisplay(success=True, message=f"= {preview}")
        return ToolResultDisplay(success=True, message="set complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Computing"

    def resolve_permission(self, args: SetArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: SetArgs, ctx: "InvokeContext | None" = None
    ) -> AsyncGenerator["ToolStreamEvent | SetResult", None]:
        handler = _DISPATCH.get(args.op)
        if handler is None:
            yield SetResult(
                ok=False, op=args.op,
                error=f"unknown op {args.op!r}. Allowed: {list(_DISPATCH)}",
            )
            return
        try:
            value, rtype = handler(args)
        except ToolError as e:
            yield SetResult(ok=False, op=args.op, error=str(e))
            return
        except Exception as e:  # noqa: BLE001
            yield SetResult(
                ok=False, op=args.op, error=f"{type(e).__name__}: {e}"
            )
            return
        yield SetResult(ok=True, op=args.op, result=value, result_type=rtype)

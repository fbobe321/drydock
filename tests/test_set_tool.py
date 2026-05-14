"""Tests for the set built-in — discrete-math set operations."""
from __future__ import annotations

import asyncio

import pytest

from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.set_tool import (
    Set, SetArgs, SetConfig, SetResult,
)


def _drive(args: SetArgs) -> SetResult:
    tool = Set(config=SetConfig(), state=BaseToolState())

    async def go() -> SetResult:
        result = None
        async for ev in tool.run(args):
            if isinstance(ev, SetResult):
                result = ev
        assert result is not None
        return result

    return asyncio.run(go())


# ── Binary set ops ─────────────────────────────────────────────────────

def test_union_of_ints():
    r = _drive(SetArgs(op="union", a="1,2,3", b="3,4,5"))
    assert r.ok and r.result == "{1, 2, 3, 4, 5}"


def test_intersection():
    r = _drive(SetArgs(op="intersection", a="1,2,3,4", b="3,4,5,6"))
    assert r.ok and r.result == "{3, 4}"


def test_difference_directional():
    """A - B: elements of A NOT in B. Different from B - A."""
    r = _drive(SetArgs(op="difference", a="1,2,3,4", b="3,4"))
    assert r.ok and r.result == "{1, 2}"
    r2 = _drive(SetArgs(op="difference", a="3,4", b="1,2,3,4"))
    assert r2.ok and r2.result == "set()"


def test_symmetric_difference():
    """A △ B = elements in exactly one of A, B."""
    r = _drive(SetArgs(op="symmetric_difference", a="1,2,3", b="2,3,4"))
    assert r.ok and r.result == "{1, 4}"


# ── Predicates ─────────────────────────────────────────────────────────

def test_is_subset_true():
    r = _drive(SetArgs(op="is_subset", a="1,2", b="1,2,3"))
    assert r.ok and r.result == "True"


def test_is_subset_false():
    r = _drive(SetArgs(op="is_subset", a="1,4", b="1,2,3"))
    assert r.ok and r.result == "False"


def test_is_subset_equal_sets():
    """A ⊆ A is always true (reflexive)."""
    r = _drive(SetArgs(op="is_subset", a="1,2,3", b="1,2,3"))
    assert r.ok and r.result == "True"


def test_is_superset():
    r = _drive(SetArgs(op="is_superset", a="1,2,3", b="2"))
    assert r.ok and r.result == "True"


def test_is_disjoint_true():
    r = _drive(SetArgs(op="is_disjoint", a="1,2", b="3,4"))
    assert r.ok and r.result == "True"


def test_is_disjoint_false():
    r = _drive(SetArgs(op="is_disjoint", a="1,2,3", b="3,4"))
    assert r.ok and r.result == "False"


# ── Cardinality / power set / product ──────────────────────────────────

def test_cardinality_with_dedup():
    """1,2,2,3 → set has 3 elements (duplicate eliminated)."""
    r = _drive(SetArgs(op="cardinality", a="1,2,2,3"))
    assert r.ok and r.result == "3"


def test_power_set_of_three():
    r = _drive(SetArgs(op="power_set", a="1,2,3"))
    assert r.ok
    # 8 subsets: ∅, {1}, {2}, {3}, {1,2}, {1,3}, {2,3}, {1,2,3}
    assert r.result.count("{") >= 8  # one for outer, 7 non-empty inner
    assert "set()" in r.result  # empty subset


def test_power_set_refuses_large():
    """Cap at |A| = 10 to avoid 1024+ subsets."""
    r = _drive(SetArgs(op="power_set", a="1,2,3,4,5,6,7,8,9,10,11"))
    assert not r.ok
    assert "cap" in r.error.lower() or "refuses" in r.error.lower()


def test_cartesian_product():
    r = _drive(SetArgs(op="product", a="1,2", b="'x','y'"))
    assert r.ok
    # 2 × 2 = 4 pairs
    assert "(1, 'x')" in r.result and "(2, 'y')" in r.result


def test_size_of_product_no_enumeration():
    """|A × B| = |A| · |B| — no enumeration, can handle large sets."""
    r = _drive(SetArgs(op="size_of_product",
                       a="1,2,3,4,5", b="a,b,c,d,e,f,g,h,i,j"))
    assert r.ok and r.result == "50"


def test_product_refuses_too_many_pairs():
    """Product capped at 1000 pairs; size_of_product gives the count."""
    a_vals = ",".join(str(i) for i in range(40))
    b_vals = ",".join(str(i) for i in range(40))  # 40 × 40 = 1600
    r = _drive(SetArgs(op="product", a=a_vals, b=b_vals))
    assert not r.ok
    assert "1000" in r.error or "cap" in r.error.lower()


# ── Mixed-type elements ────────────────────────────────────────────────

def test_string_elements():
    r = _drive(SetArgs(op="union", a="'apple','banana'", b="'banana','cherry'"))
    assert r.ok
    assert "'apple'" in r.result and "'cherry'" in r.result


def test_bareword_elements():
    """Unquoted identifiers like `x, y, z` become string tokens."""
    r = _drive(SetArgs(op="union", a="x,y,z", b="y,z,w"))
    assert r.ok
    assert "'w'" in r.result and "'x'" in r.result


# ── Input parsing edge cases ──────────────────────────────────────────

def test_empty_set_rejected():
    r = _drive(SetArgs(op="cardinality", a=""))
    assert not r.ok


def test_too_long_input_rejected():
    r = _drive(SetArgs(op="cardinality", a="1," * 5000))
    assert not r.ok


# ── Discovery ──────────────────────────────────────────────────────────

def test_tool_is_discoverable():
    from drydock.core.paths import DEFAULT_TOOL_DIR
    from drydock.core.tools.manager import ToolManager
    classes = list(ToolManager._iter_tool_classes([DEFAULT_TOOL_DIR.path]))
    names = {c.get_name() for c in classes}
    assert "set" in names

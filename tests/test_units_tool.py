"""Tests for the units built-in — sympy.physics.units wrapper."""
from __future__ import annotations

import asyncio

import pytest

from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.units_tool import (
    Units, UnitsArgs, UnitsConfig, UnitsResult,
)


def _drive(args: UnitsArgs) -> UnitsResult:
    tool = Units(config=UnitsConfig(), state=BaseToolState())

    async def go() -> UnitsResult:
        result = None
        async for ev in tool.run(args):
            if isinstance(ev, UnitsResult):
                result = ev
        assert result is not None
        return result

    return asyncio.run(go())


# ── Convert ────────────────────────────────────────────────────────────

def test_convert_mile_to_km():
    """1 mile = 1.609344 km (1609.344 m exact = 1.609344 km)."""
    r = _drive(UnitsArgs(op="convert", expression="1*mile", to_unit="kilometer"))
    assert r.ok
    # sympy returns the exact ratio 25146/15625 (= 1.609344)
    assert "25146" in r.result or "1.609" in r.result


def test_convert_mph_to_mps():
    """60 mph = 26.8224 m/s = 16764/625 m/s"""
    r = _drive(UnitsArgs(op="convert", expression="60*mile/hour",
                         to_unit="meter/second"))
    assert r.ok
    assert "16764" in r.result or "26.8" in r.result


def test_convert_force():
    """9.81 kg·m/s² = 9.81 N"""
    r = _drive(UnitsArgs(op="convert", expression="9.81*kg*meter/second**2",
                         to_unit="newton"))
    assert r.ok and "9.81" in r.result and "newton" in r.result


def test_convert_atm_to_pa():
    """1 atm = 101325 Pa (exact)."""
    r = _drive(UnitsArgs(op="convert", expression="1*atmosphere",
                         to_unit="pascal"))
    assert r.ok and "101325" in r.result


def test_convert_uses_abbreviations():
    """Both 'm' and 'meter' should work."""
    r = _drive(UnitsArgs(op="convert", expression="5*m", to_unit="cm"))
    assert r.ok and "500" in r.result


# ── Evaluate ──────────────────────────────────────────────────────────

def test_evaluate_kinetic_energy():
    """KE = ½ m v² with m=1 kg, v=3 m/s ⇒ 4.5 kg·m²/s² (= 4.5 J)"""
    r = _drive(UnitsArgs(op="evaluate",
                         expression="Rational(1,2)*kg*(3*meter/second)**2"))
    assert r.ok
    assert "kilogram" in r.result and "meter**2" in r.result


def test_evaluate_compound_units():
    """5 N · 2 m = 10 N·m (= 10 J)."""
    r = _drive(UnitsArgs(op="evaluate", expression="5*newton * 2*meter"))
    assert r.ok and "10" in r.result


# ── Dimension ─────────────────────────────────────────────────────────

def test_dimension_of_energy():
    r = _drive(UnitsArgs(op="dimension", expression="joule"))
    assert r.ok and "energy" in r.result


def test_dimension_of_speed():
    r = _drive(UnitsArgs(op="dimension", expression="meter/second"))
    assert r.ok
    # "velocity" or "length/time" depending on sympy version
    assert "velocity" in r.result.lower() or "length/time" in r.result


# ── Consistent (dimensional equivalence) ──────────────────────────────

def test_joule_equals_newton_meter():
    """Critical: J ≡ N·m even though sympy names them differently."""
    r = _drive(UnitsArgs(op="consistent",
                         expression="joule", expression2="newton*meter"))
    assert r.ok
    assert r.result.startswith("True")


def test_joule_differs_from_newton():
    """Sanity: J has L²·M·T⁻², N has L·M·T⁻². Different."""
    r = _drive(UnitsArgs(op="consistent",
                         expression="joule", expression2="newton"))
    assert r.ok
    assert r.result.startswith("False")


def test_watt_equals_joule_per_second():
    r = _drive(UnitsArgs(op="consistent",
                         expression="watt", expression2="joule/second"))
    assert r.ok and r.result.startswith("True")


def test_consistent_requires_expression2():
    r = _drive(UnitsArgs(op="consistent", expression="meter"))
    assert not r.ok


# ── to_si ─────────────────────────────────────────────────────────────

def test_to_si_energy_to_kg_m2_per_s2():
    r = _drive(UnitsArgs(op="to_si", expression="joule"))
    assert r.ok
    # joule = kg·m²/s²
    assert "kilogram" in r.result and "meter**2" in r.result


def test_to_si_keeps_numeric_factor():
    r = _drive(UnitsArgs(op="to_si", expression="1000*joule"))
    assert r.ok and "1000" in r.result


# ── list_units ────────────────────────────────────────────────────────

def test_list_units_length():
    r = _drive(UnitsArgs(op="list_units", category="length"))
    assert r.ok and "meter" in r.result and "mile" in r.result


def test_list_units_no_category_shows_categories():
    r = _drive(UnitsArgs(op="list_units"))
    assert r.ok and "length" in r.result and "energy" in r.result


def test_list_units_unknown_category():
    r = _drive(UnitsArgs(op="list_units", category="nonsense"))
    assert not r.ok


# ── Sandbox ───────────────────────────────────────────────────────────

def test_rejects_import():
    r = _drive(UnitsArgs(op="evaluate", expression="import os"))
    assert not r.ok


def test_rejects_unknown_unit():
    r = _drive(UnitsArgs(op="evaluate", expression="5 * banana"))
    assert not r.ok
    assert "banana" in r.error or "unknown" in r.error.lower()


def test_rejects_attribute_access():
    r = _drive(UnitsArgs(op="evaluate", expression="meter.value"))
    assert not r.ok


# ── Discovery ─────────────────────────────────────────────────────────

def test_tool_is_discoverable():
    from drydock.core.paths import DEFAULT_TOOL_DIR
    from drydock.core.tools.manager import ToolManager
    classes = list(ToolManager._iter_tool_classes([DEFAULT_TOOL_DIR.path]))
    names = {c.get_name() for c in classes}
    assert "units" in names

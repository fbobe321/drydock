"""Tests for the chemistry built-in."""
from __future__ import annotations

import asyncio

import pytest

from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.chemistry_tool import (
    Chemistry, ChemistryArgs, ChemistryConfig, ChemistryResult,
)


def _drive(args: ChemistryArgs) -> ChemistryResult:
    tool = Chemistry(config=ChemistryConfig(), state=BaseToolState())

    async def go() -> ChemistryResult:
        result = None
        async for ev in tool.run(args):
            if isinstance(ev, ChemistryResult):
                result = ev
        assert result is not None
        return result

    return asyncio.run(go())


# ── Element lookup ────────────────────────────────────────────────────

def test_element_by_symbol():
    r = _drive(ChemistryArgs(op="element", name="Na"))
    assert r.ok
    assert "Sodium" in r.result and "Z=11" in r.result


def test_element_by_name():
    r = _drive(ChemistryArgs(op="element", name="sodium"))
    assert r.ok and "Na" in r.result


def test_element_by_atomic_number():
    r = _drive(ChemistryArgs(op="element", name="11"))
    assert r.ok and "Sodium" in r.result


def test_element_unknown():
    r = _drive(ChemistryArgs(op="element", name="banana"))
    assert not r.ok


def test_element_symbol_is_case_sensitive():
    """`NA` is NOT Na — symbols match exactly. Lowercase 'na' isn't a
    valid name either, so this should fail."""
    r = _drive(ChemistryArgs(op="element", name="na"))
    assert not r.ok


def test_element_z_out_of_range():
    r = _drive(ChemistryArgs(op="element", name="200"))
    assert not r.ok


# ── Molar mass ────────────────────────────────────────────────────────

def test_molar_mass_water():
    """H2O ≈ 18.015 g/mol"""
    r = _drive(ChemistryArgs(op="molar_mass", formula="H2O"))
    assert r.ok
    assert "18" in r.result
    val = float(r.result.split()[0])
    assert abs(val - 18.015) < 0.01


def test_molar_mass_glucose():
    """C6H12O6 ≈ 180.156 g/mol"""
    r = _drive(ChemistryArgs(op="molar_mass", formula="C6H12O6"))
    assert r.ok
    val = float(r.result.split()[0])
    assert abs(val - 180.156) < 0.01


def test_molar_mass_with_nested_parens():
    """Ca(OH)2: Ca + 2O + 2H = 40.078 + 2*(15.999) + 2*(1.008) ≈ 74.09"""
    r = _drive(ChemistryArgs(op="molar_mass", formula="Ca(OH)2"))
    assert r.ok
    val = float(r.result.split()[0])
    assert abs(val - 74.092) < 0.01


def test_molar_mass_iron_sulfate():
    """Fe2(SO4)3: 2(55.845) + 3(32.06) + 12(15.999) ≈ 399.86"""
    r = _drive(ChemistryArgs(op="molar_mass", formula="Fe2(SO4)3"))
    assert r.ok
    val = float(r.result.split()[0])
    assert abs(val - 399.86) < 0.05


def test_molar_mass_bad_formula():
    r = _drive(ChemistryArgs(op="molar_mass", formula="banana"))
    assert not r.ok


def test_molar_mass_unbalanced_parens():
    r = _drive(ChemistryArgs(op="molar_mass", formula="Ca(OH2"))
    assert not r.ok


def test_molar_mass_unknown_element_symbol():
    r = _drive(ChemistryArgs(op="molar_mass", formula="XyZ2"))
    assert not r.ok


# ── Mole conversions ────────────────────────────────────────────────

def test_moles_from_grams_water():
    """18 g H2O / 18 g/mol ≈ 1 mole"""
    r = _drive(ChemistryArgs(op="moles_from_grams", formula="H2O", grams="18"))
    assert r.ok
    val = float(r.result.split()[0])
    assert abs(val - 0.999) < 0.01


def test_grams_from_moles_nacl():
    """2 mol NaCl × 58.44 g/mol ≈ 116.88 g"""
    r = _drive(ChemistryArgs(op="grams_from_moles", formula="NaCl", moles="2"))
    assert r.ok
    val = float(r.result.split()[0])
    assert abs(val - 116.88) < 0.05


def test_particles_from_one_mole_is_avogadro():
    r = _drive(ChemistryArgs(op="particles_from_moles", moles="1"))
    assert r.ok
    # Avogadro = 6.022e23
    val = float(r.result.split()[0])
    assert abs(val - 6.022e23) < 1e20


def test_moles_from_one_avogadro_is_one_mole():
    r = _drive(ChemistryArgs(op="moles_from_particles", particles="6.02214076e23"))
    assert r.ok
    val = float(r.result.split()[0])
    assert abs(val - 1.0) < 1e-5


# ── Percent composition ───────────────────────────────────────────────

def test_percent_composition_water():
    """H2O: H = 2*1.008/18.015 ≈ 11.19%, O = 88.81%"""
    r = _drive(ChemistryArgs(op="percent_composition", formula="H2O"))
    assert r.ok
    assert "H=11" in r.result and "O=88" in r.result


def test_percent_composition_glucose():
    """C6H12O6: C ≈ 40%, H ≈ 6.7%, O ≈ 53.3%"""
    r = _drive(ChemistryArgs(op="percent_composition", formula="C6H12O6"))
    assert r.ok
    # Extract C percent
    import re
    m = re.search(r"C=([\d.]+)", r.result)
    assert m and abs(float(m.group(1)) - 40.0) < 0.5


# ── Empirical formula ────────────────────────────────────────────────

def test_empirical_formula_glucose():
    """C=40, H=6.7, O=53.3 → CH2O (glucose's empirical formula)"""
    r = _drive(ChemistryArgs(op="empirical_formula",
                             percents="C=40.0, H=6.7, O=53.3"))
    assert r.ok and r.result == "CH2O"


def test_empirical_formula_benzene_like():
    """C=92.3, H=7.7 → CH (benzene's empirical formula)"""
    r = _drive(ChemistryArgs(op="empirical_formula",
                             percents="C=92.3, H=7.7"))
    assert r.ok and r.result == "CH"


def test_empirical_formula_bad_input():
    r = _drive(ChemistryArgs(op="empirical_formula", percents="not real"))
    assert not r.ok


# ── Discovery ──────────────────────────────────────────────────────────

def test_tool_is_discoverable():
    from drydock.core.paths import DEFAULT_TOOL_DIR
    from drydock.core.tools.manager import ToolManager
    classes = list(ToolManager._iter_tool_classes([DEFAULT_TOOL_DIR.path]))
    names = {c.get_name() for c in classes}
    assert "chemistry" in names

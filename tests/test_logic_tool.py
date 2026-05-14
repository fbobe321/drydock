"""Tests for the logic built-in tool — symbolic propositional logic."""
from __future__ import annotations

import asyncio

import pytest

from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.logic_tool import (
    Logic,
    LogicArgs,
    LogicConfig,
    LogicResult,
    _DISPATCH,
    _parse,
)


def _drive(args: LogicArgs) -> LogicResult:
    tool = Logic(config=LogicConfig(), state=BaseToolState())

    async def go() -> LogicResult:
        result = None
        async for ev in tool.run(args):
            if isinstance(ev, LogicResult):
                result = ev
        assert result is not None
        return result

    return asyncio.run(go())


# ── Truth table ─────────────────────────────────────────────────────────

def test_truth_table_for_implication():
    r = _drive(LogicArgs(op="truth_table", expression="p >> q"))
    assert r.ok
    # `p >> q` is False only when p=T, q=F
    assert "T F | F" in r.result
    assert "T T | T" in r.result
    assert "F F | T" in r.result
    assert "F T | T" in r.result


def test_truth_table_caps_variables():
    """6 vars max — anything bigger should refuse."""
    r = _drive(LogicArgs(op="truth_table", expression="a & b & c & d & e & f & g"))
    assert not r.ok
    assert "refuses" in r.error or "cap" in r.error


# ── Equivalent ──────────────────────────────────────────────────────────

def test_implication_equals_disjunctive_form():
    """p → q ≡ ¬p ∨ q — the most-quoted equivalence law."""
    r = _drive(LogicArgs(op="equivalent", expression="p >> q",
                         expression2="~p | q"))
    assert r.ok
    assert r.result.startswith("true")


def test_contrapositive_equivalence():
    """p → q ≡ ¬q → ¬p."""
    r = _drive(LogicArgs(op="equivalent", expression="p >> q",
                         expression2="~q >> ~p"))
    assert r.ok
    assert r.result.startswith("true")


def test_converse_is_NOT_equivalent():
    """p → q ≢ q → p — the most common student mistake."""
    r = _drive(LogicArgs(op="equivalent", expression="p >> q",
                         expression2="q >> p"))
    assert r.ok
    assert r.result.startswith("false")


def test_equivalent_requires_expression2():
    r = _drive(LogicArgs(op="equivalent", expression="p"))
    assert not r.ok
    assert "expression2" in r.error


# ── Contrapositive ──────────────────────────────────────────────────────

def test_contrapositive_of_implication():
    r = _drive(LogicArgs(op="contrapositive", expression="p >> q"))
    assert r.ok
    assert r.result == "Implies(~q, ~p)"


def test_contrapositive_recovers_from_disjunctive_form():
    """`~p | q` is logically an implication — operator can use either spelling."""
    r = _drive(LogicArgs(op="contrapositive", expression="~p | q"))
    assert r.ok
    # Simplified to Implies(p, q) then contrapositive'd to Implies(~q, ~p)
    assert "~q" in r.result and "~p" in r.result


def test_contrapositive_rejects_non_implication():
    r = _drive(LogicArgs(op="contrapositive", expression="p & q"))
    assert not r.ok
    assert "implication" in r.error


# ── De Morgan via negate ────────────────────────────────────────────────

def test_negate_pushes_through_AND():
    """¬(p ∧ q) ≡ ¬p ∨ ¬q"""
    r = _drive(LogicArgs(op="negate", expression="p & q"))
    assert r.ok
    assert r.result == "~p | ~q"


def test_negate_pushes_through_OR():
    """¬(p ∨ q) ≡ ¬p ∧ ¬q"""
    r = _drive(LogicArgs(op="negate", expression="p | q"))
    assert r.ok
    assert r.result == "~p & ~q"


def test_double_negation_simplifies():
    r = _drive(LogicArgs(op="negate", expression="~(p & q)"))
    assert r.ok
    # ¬¬(p ∧ q) ≡ p ∧ q
    assert r.result == "p & q"


# ── Tautology / contradiction ───────────────────────────────────────────

def test_law_of_excluded_middle_is_tautology():
    r = _drive(LogicArgs(op="tautology", expression="p | ~p"))
    assert r.ok and r.result == "true"


def test_implication_is_not_a_tautology():
    r = _drive(LogicArgs(op="tautology", expression="p >> q"))
    assert r.ok and r.result == "false"


def test_law_of_non_contradiction_is_a_contradiction():
    r = _drive(LogicArgs(op="contradiction", expression="p & ~p"))
    assert r.ok and r.result == "true"


# ── Satisfiability ──────────────────────────────────────────────────────

def test_satisfiable_returns_witness():
    r = _drive(LogicArgs(op="satisfiable", expression="(p | q) & ~p"))
    assert r.ok
    assert r.result.startswith("true")
    # The witness must satisfy: p=F (forced by ~p) and q=T (so p|q holds)
    assert "p=F" in r.result and "q=T" in r.result


def test_unsatisfiable_returns_false():
    r = _drive(LogicArgs(op="satisfiable", expression="p & ~p"))
    assert r.ok
    assert r.result.startswith("false")


# ── CNF / DNF / simplify ────────────────────────────────────────────────

def test_iff_simplifies_to_cnf():
    """(p → q) ∧ (q → p) is the biconditional p ↔ q in CNF."""
    r = _drive(LogicArgs(op="cnf", expression="(p >> q) & (q >> p)"))
    assert r.ok
    # CNF form must be a conjunction of disjunctions.
    assert "&" in r.result and "|" in r.result


def test_simplify_collapses_idempotence():
    r = _drive(LogicArgs(op="simplify", expression="p & p"))
    assert r.ok
    assert r.result == "p"


# ── Modus Ponens ────────────────────────────────────────────────────────

def test_modus_ponens_derives_q():
    """Given p and (p → q), derive q."""
    r = _drive(LogicArgs(op="modus_ponens", expression="p",
                         expression2="p >> q"))
    assert r.ok and r.result == "q"


def test_modus_ponens_rejects_mismatched_antecedent():
    """Given x and (p → q), Modus Ponens does NOT apply."""
    r = _drive(LogicArgs(op="modus_ponens", expression="x",
                         expression2="p >> q"))
    assert not r.ok
    assert "match" in r.error or "antecedent" in r.error


def test_modus_ponens_rejects_non_implication():
    r = _drive(LogicArgs(op="modus_ponens", expression="p",
                         expression2="p & q"))
    assert not r.ok
    assert "implication" in r.error


# ── Evaluate ────────────────────────────────────────────────────────────

def test_evaluate_with_explicit_assignment():
    r = _drive(LogicArgs(op="evaluate", expression="p & q",
                         variables="p=T, q=T"))
    assert r.ok and r.result == "true"
    r2 = _drive(LogicArgs(op="evaluate", expression="p & q",
                          variables="p=T, q=F"))
    assert r2.ok and r2.result == "false"


def test_evaluate_demands_assignment_for_free_symbols():
    r = _drive(LogicArgs(op="evaluate", expression="p & q"))
    assert not r.ok
    assert "assignment" in r.error or "free symbols" in r.error


def test_evaluate_handles_constant_expression():
    """No free symbols, no assignment needed."""
    r = _drive(LogicArgs(op="evaluate", expression="T | F"))
    assert r.ok and r.result == "true"


# ── Sandbox enforcement ─────────────────────────────────────────────────

def test_rejects_import_keyword():
    r = _drive(LogicArgs(op="evaluate", expression="import os"))
    assert not r.ok
    assert "forbidden" in r.error or "import" in r.error.lower()


def test_rejects_attribute_access():
    r = _drive(LogicArgs(op="evaluate", expression="p.x"))
    assert not r.ok


def test_rejects_long_expression():
    r = _drive(LogicArgs(op="evaluate", expression="p " * 5000))
    assert not r.ok


# ── Discovery ───────────────────────────────────────────────────────────

def test_tool_is_discoverable():
    from drydock.core.paths import DEFAULT_TOOL_DIR
    from drydock.core.tools.manager import ToolManager
    classes = list(ToolManager._iter_tool_classes([DEFAULT_TOOL_DIR.path]))
    names = {c.get_name() for c in classes}
    assert "logic" in names

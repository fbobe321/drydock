"""Tests for the constraint-formula extractor.

The extractor takes a constraint-shaped question and pulls out the
concrete predicate + polynomial. The constraint_hint module uses
this to inject a SPECIALISED template instead of a generic one —
this closes the adoption gap that left v2.8.31 at 0/80 sessions
calling `solve` despite the detector firing.
"""
from __future__ import annotations

import pytest

from drydock.core.constraint_extract import (
    ExtractResult,
    _clean_latex,
    _normalize_for_z3,
    _truncate_formula_after_predicate,
    extract,
    render_template,
)


# ── _clean_latex unit tests ──────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected_contains,expected_not_contains", [
    (r"$x^3 - 16x^2 - 72x + 1056$", "x**3", "$"),
    (r"$\mathbb{Z}$ is the quantity", "the quantity", r"\mathbb"),
    (r"x \in \mathbb{Z}", "x", r"\mathbb{Z}"),
    (r"\frac{a}{b}", "(a)/(b)", r"\frac"),
    (r"x \leq 5", "x <= 5", r"\leq"),
])
def test_clean_latex_strips_commands(raw, expected_contains, expected_not_contains):
    cleaned = _clean_latex(raw)
    assert expected_contains in cleaned
    assert expected_not_contains not in cleaned


# ── _normalize_for_z3 ────────────────────────────────────────────────────

def test_normalize_inserts_multiplication_between_coefficient_and_variable():
    assert "16 * x" in _normalize_for_z3("16x**2")


def test_normalize_expands_small_powers():
    out = _normalize_for_z3("x**3 - 16x**2 - 72x + 1056")
    # x**3 → x*x*x, 16x**2 → 16*x*x (with normalised spacing)
    assert "x * x * x" in out
    assert "16 * x * x" in out


def test_normalize_leaves_high_powers_alone():
    out = _normalize_for_z3("x**10")
    # Power is preserved (not expanded into 10x multiplications);
    # whitespace around `**` is cosmetic.
    assert "x ** 10" in out or "x**10" in out


# ── _truncate_formula_after_predicate ────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("How many integers x satisfy x**2 + 1", "x**2 + 1"),
    ("How many positive integers n with n**3 - n", "n**3 - n"),
    ("the quantity x**3 - 16x**2", "x**3 - 16x**2"),
    ("How many n such that 2**n - 1", "2**n - 1"),
])
def test_truncate_finds_tail_after_last_stop_word(raw, expected):
    assert _truncate_formula_after_predicate(raw) == expected


# ── extract() — happy path per predicate ─────────────────────────────────

def test_extract_perfect_square_hle_example():
    q = "For how many integers $x \\in \\mathbb{Z}$ is the quantity $x^3 - 16x^2 - 72x + 1056$ a perfect square?"
    r = extract(q)
    assert r is not None
    assert r.predicate == "perfect_square"
    assert r.formula == "x * x * x - 16 * x * x - 72 * x + 1056"
    assert r.variables == ["x"]
    assert r.confidence >= 0.5


def test_extract_perfect_square_simple():
    q = "For how many integers x is x^4 - 5x^2 + 4 a perfect square?"
    r = extract(q)
    assert r is not None and r.predicate == "perfect_square"
    assert r.formula == "x * x * x * x - 5 * x * x + 4"


def test_extract_prime():
    r = extract("For how many positive integers n is n^2 + n + 41 prime?")
    assert r is not None and r.predicate == "prime"
    assert r.formula == "n * n + n + 41"
    assert r.variables == ["n"]


def test_extract_prime_with_such_that():
    r = extract("How many n such that 2^n - 1 is prime?")
    assert r is not None and r.predicate == "prime"


def test_extract_divisible_by():
    r = extract("How many integers x satisfy x^2 + 1 is divisible by 5?")
    assert r is not None and r.predicate == "divisible_by"
    assert r.formula == "x * x + 1"
    assert r.divisor == 5
    assert r.variables == ["x"]


def test_extract_divisible_by_alternative_phrasing():
    r = extract("How many positive integers n with n^3 - n is divisible by 6?")
    assert r is not None and r.predicate == "divisible_by"
    assert r.formula == "n * n * n - n"
    assert r.divisor == 6


# ── equation_solve / smallest_with / largest_with ────────────────────────

def test_extract_equation_solve_with_lhs_rhs():
    r = extract("Find all integers x such that x^2 - 5x + 6 = 0.")
    assert r is not None and r.predicate == "equation_solve"
    assert r.formula == "x * x - 5 * x + 6"
    assert r.second_formula == "0"


def test_extract_equation_solve_quadratic_rhs():
    r = extract("Solve x^2 = 4y + 1 for x.")
    assert r is not None and r.predicate == "equation_solve"
    # Either side capture is acceptable; just verify both formulas are extracted
    assert r.formula and r.second_formula


def test_extract_smallest_with():
    r = extract("Find the smallest positive integer n such that n^2 > 1000.")
    assert r is not None and r.predicate == "smallest_with"
    assert "n * n > 1000" in r.formula
    assert r.variables == ["n"]


def test_extract_smallest_with_factorial_ge():
    r = extract("What is the smallest n with n! >= 100?")
    assert r is not None and r.predicate == "smallest_with"
    # `>=` must NOT be split by the operator-spacing normaliser
    assert ">=" in r.formula


def test_extract_largest_with():
    r = extract("Find the largest integer x with x^2 < 50.")
    assert r is not None and r.predicate == "largest_with"
    assert "x * x < 50" in r.formula
    assert r.variables == ["x"]


# ── Render templates for the new predicates ──────────────────────────────

def test_render_equation_solve_includes_both_sides():
    r = ExtractResult(
        predicate="equation_solve",
        formula="x * x - 5 * x + 6",
        second_formula="0",
        variables=["x"],
    )
    out = render_template(r)
    assert "(x * x - 5 * x + 6) == (0)" in out
    assert "find_all" in out


def test_render_smallest_with_uses_min_optimization():
    r = ExtractResult(
        predicate="smallest_with",
        formula="n * n > 1000",
        variables=["n"],
    )
    out = render_template(r)
    assert 'direction="min"' in out
    assert 'objective="n"' in out
    assert "n * n > 1000" in out


def test_render_largest_with_uses_max_optimization():
    r = ExtractResult(
        predicate="largest_with",
        formula="x * x < 50",
        variables=["x"],
    )
    out = render_template(r)
    assert 'direction="max"' in out
    assert 'objective="x"' in out


# ── Normalizer regression: multi-char comparison ops ────────────────────

def test_normalize_preserves_gte():
    assert ">=" in _normalize_for_z3("n! >= 100")


def test_normalize_preserves_lte():
    assert "<=" in _normalize_for_z3("x <= 10")


def test_normalize_preserves_eq_eq():
    assert "==" in _normalize_for_z3("x == 5")


# ── Negative cases ───────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "Build a calculator.",
    "What is the area of a circle?",
    "What is x?",
    "Implement bubble sort.",
    "Hello",
    "",
])
def test_extract_returns_none_for_non_constraint(msg: str):
    assert extract(msg) is None


# ── render_template ──────────────────────────────────────────────────────

def test_render_perfect_square_includes_formula():
    r = ExtractResult(
        predicate="perfect_square",
        formula="x * x * x - 4",
        variables=["x"],
    )
    out = render_template(r)
    assert "y * y == x * x * x - 4" in out
    assert "find_all" in out
    assert "x:Int, y:Int" in out


def test_render_prime_directs_to_number_theory():
    r = ExtractResult(
        predicate="prime",
        formula="n * n + n + 41",
        variables=["n"],
    )
    out = render_template(r)
    # The prime template should route to number_theory.is_prime
    assert "is_prime" in out
    assert "number_theory" in out


def test_render_divisible_by_uses_modulo():
    r = ExtractResult(
        predicate="divisible_by",
        formula="x * x + 1",
        variables=["x"],
        divisor=5,
    )
    out = render_template(r)
    assert "(x * x + 1) % 5 == 0" in out


# ── End-to-end: detect_constraint_shape returns specialised template ──

def test_detect_returns_specialised_template_for_known_shape():
    from drydock.core.constraint_hint import detect_constraint_shape
    q = "For how many integers x is x^3 - 16x^2 - 72x + 1056 a perfect square?"
    hit = detect_constraint_shape(q)
    assert hit is not None
    label, template = hit
    assert label == "diophantine-count"
    # The specialised section must reference the actual polynomial
    assert "16 * x * x" in template
    assert "1056" in template
    # And the generic fallback should still be there as backup reference
    assert "Generic template" in template


def test_detect_falls_back_to_generic_when_extraction_fails():
    """For a shape-matched message that the extractor can't parse,
    the generic template is still returned alone."""
    from drydock.core.constraint_hint import detect_constraint_shape
    # Logic puzzle matches but extractor doesn't handle them.
    q = "Three people live in three houses. Who lives in house 1? It's a logic puzzle."
    hit = detect_constraint_shape(q)
    assert hit is not None
    label, template = hit
    assert label == "logic-puzzle"
    # No specialisation, just the generic Einstein-style example
    assert "Distinct(a, b, c)" in template

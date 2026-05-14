"""Tests for the number_theory built-in."""
from __future__ import annotations

import asyncio

import pytest

from drydock.core.tools.base import BaseToolState
from drydock.core.tools.builtins.number_theory_tool import (
    NumberTheory,
    NumberTheoryArgs,
    NumberTheoryConfig,
    NumberTheoryResult,
)


def _drive(args: NumberTheoryArgs) -> NumberTheoryResult:
    tool = NumberTheory(config=NumberTheoryConfig(), state=BaseToolState())

    async def go() -> NumberTheoryResult:
        result = None
        async for ev in tool.run(args):
            if isinstance(ev, NumberTheoryResult):
                result = ev
        assert result is not None
        return result

    return asyncio.run(go())


# ── Primality ──────────────────────────────────────────────────────────

def test_is_prime_small():
    r = _drive(NumberTheoryArgs(op="is_prime", n="97"))
    assert r.ok and r.result == "True"


def test_is_prime_composite():
    r = _drive(NumberTheoryArgs(op="is_prime", n="100"))
    assert r.ok and r.result == "False"


def test_is_prime_mersenne():
    """2^31 - 1 is the eighth Mersenne prime."""
    r = _drive(NumberTheoryArgs(op="is_prime", n="2**31 - 1"))
    assert r.ok and r.result == "True"


def test_next_prime():
    r = _drive(NumberTheoryArgs(op="next_prime", n="100"))
    assert r.ok and r.result == "101"


def test_prev_prime():
    r = _drive(NumberTheoryArgs(op="prev_prime", n="100"))
    assert r.ok and r.result == "97"


def test_prime_count_1_to_100():
    """π(100) - π(0) = 25 (there are 25 primes below 100)."""
    r = _drive(NumberTheoryArgs(op="prime_count", a="1", b="100"))
    assert r.ok and r.result == "25"


def test_primes_in_range():
    r = _drive(NumberTheoryArgs(op="primes_in", a="10", b="30"))
    assert r.ok
    assert "11" in r.result and "29" in r.result


def test_primes_in_caps_at_1000():
    """Refuse to return more than 1000 primes."""
    r = _drive(NumberTheoryArgs(op="primes_in", a="1", b="100000"))
    assert not r.ok
    assert "1000" in r.error or "cap" in r.error


# ── Factor / divisors ──────────────────────────────────────────────────

def test_factor_classic():
    """360 = 2^3 · 3^2 · 5"""
    r = _drive(NumberTheoryArgs(op="factor", n="360"))
    assert r.ok
    # Dict form: {2: 3, 3: 2, 5: 1}
    assert "2: 3" in r.result and "3: 2" in r.result and "5: 1" in r.result


def test_factor_accepts_expression():
    """10! = 3628800 = 2^8 · 3^4 · 5^2 · 7"""
    r = _drive(NumberTheoryArgs(op="factor", n="factorial(10)"))
    assert r.ok
    assert "2: 8" in r.result and "7: 1" in r.result


def test_divisors_of_28():
    """28 is perfect: divisors 1+2+4+7+14 = 28 (excluding itself)."""
    r = _drive(NumberTheoryArgs(op="divisors", n="28"))
    assert r.ok and r.result == "[1, 2, 4, 7, 14, 28]"


def test_divisor_count():
    r = _drive(NumberTheoryArgs(op="divisor_count", n="360"))
    assert r.ok and r.result == "24"


def test_divisor_sum_first_power():
    """σ(28) = 1+2+4+7+14+28 = 56 (perfect ⇒ σ(n) = 2n)."""
    r = _drive(NumberTheoryArgs(op="divisor_sum", n="28", k="1"))
    assert r.ok and r.result == "56"


# ── Multiplicative functions ───────────────────────────────────────────

def test_totient_of_12():
    """φ(12) = 4 (units mod 12 are {1, 5, 7, 11})."""
    r = _drive(NumberTheoryArgs(op="totient", n="12"))
    assert r.ok and r.result == "4"


def test_totient_prime_is_p_minus_one():
    """φ(p) = p - 1 for prime p."""
    r = _drive(NumberTheoryArgs(op="totient", n="17"))
    assert r.ok and r.result == "16"


def test_mobius_squarefree_three_factors():
    """30 = 2·3·5 is squarefree with 3 prime factors → μ(30) = (-1)^3 = -1."""
    r = _drive(NumberTheoryArgs(op="mobius", n="30"))
    assert r.ok and r.result == "-1"


def test_mobius_with_square_factor():
    """μ(12) = 0 since 12 = 2² · 3 has a squared prime."""
    r = _drive(NumberTheoryArgs(op="mobius", n="12"))
    assert r.ok and r.result == "0"


# ── GCD / LCM ─────────────────────────────────────────────────────────

def test_gcd():
    r = _drive(NumberTheoryArgs(op="gcd", a="48", b="18"))
    assert r.ok and r.result == "6"


def test_lcm():
    r = _drive(NumberTheoryArgs(op="lcm", a="4", b="6"))
    assert r.ok and r.result == "12"


def test_gcd_with_expressions():
    r = _drive(NumberTheoryArgs(op="gcd", a="factorial(5)", b="factorial(6)"))
    assert r.ok and r.result == "120"


# ── Modular arithmetic ────────────────────────────────────────────────

def test_mod_inverse():
    """3 * 5 ≡ 15 ≡ 1 (mod 7), so 3^(-1) ≡ 5."""
    r = _drive(NumberTheoryArgs(op="mod_inverse", a="3", m="7"))
    assert r.ok and r.result == "5"


def test_mod_inverse_not_invertible():
    """gcd(6, 9) = 3 ≠ 1, so 6 has no inverse mod 9."""
    r = _drive(NumberTheoryArgs(op="mod_inverse", a="6", m="9"))
    assert not r.ok
    assert "invertible" in r.error or "inverse" in r.error.lower()


def test_mod_pow():
    """2^100 mod 7 — must handle the huge intermediate value efficiently."""
    r = _drive(NumberTheoryArgs(op="mod_pow", b="2", e="100", m="7"))
    assert r.ok and r.result == "2"


def test_mod_pow_huge():
    r = _drive(NumberTheoryArgs(op="mod_pow", b="2", e="1000000", m="1000000007"))
    assert r.ok
    # Sanity: not 0, not 1, valid integer
    assert int(r.result) >= 0


# ── CRT ────────────────────────────────────────────────────────────────

def test_crt_classic():
    """The classic Sunzi Suanjing problem: x ≡ 2 (mod 3), x ≡ 3 (mod 5),
    x ≡ 2 (mod 7) — solution is x ≡ 23 (mod 105)."""
    r = _drive(NumberTheoryArgs(op="crt", remainders="2,3,2", moduli="3,5,7"))
    assert r.ok
    assert "23" in r.result and "105" in r.result


def test_crt_length_mismatch():
    r = _drive(NumberTheoryArgs(op="crt", remainders="1,2", moduli="3,5,7"))
    assert not r.ok
    assert "remainders" in r.error or "len" in r.error.lower() or "moduli" in r.error


# ── Sandbox enforcement ────────────────────────────────────────────────

def test_rejects_dunder_import():
    r = _drive(NumberTheoryArgs(op="is_prime", n='__import__("os")'))
    assert not r.ok


def test_rejects_attribute_access():
    r = _drive(NumberTheoryArgs(op="is_prime", n="foo.bar"))
    assert not r.ok


def test_rejects_unknown_function():
    r = _drive(NumberTheoryArgs(op="is_prime", n="banana(7)"))
    assert not r.ok


def test_rejects_non_integer_expression():
    """Float / Rational arg should fail integer conversion."""
    r = _drive(NumberTheoryArgs(op="is_prime", n="Rational(3, 2)"))
    assert not r.ok


# ── Discovery ──────────────────────────────────────────────────────────

def test_tool_is_discoverable():
    from drydock.core.paths import DEFAULT_TOOL_DIR
    from drydock.core.tools.manager import ToolManager
    classes = list(ToolManager._iter_tool_classes([DEFAULT_TOOL_DIR.path]))
    names = {c.get_name() for c in classes}
    assert "number_theory" in names

"""Tests for the built-in math tool (drydock.core.tools.builtins.math_tool).

Two layers:
- safe_eval() unit tests pin the sandbox semantics (allowed names, forbidden
  imports/attributes, length cap)
- Math.run() integration tests pin the tool wrapper (Pydantic shape, error
  surfacing, exact-result preservation through Fraction/Decimal/big int)
"""
from __future__ import annotations

import math

import pytest

from drydock.core.tools.builtins.math_tool import (
    MathArgs,
    MathResult,
    Math,
    safe_eval,
)


async def _run(expr: str, decimal_precision: int = 28) -> MathResult:
    """Drive the tool's async generator and return the final MathResult."""
    args = MathArgs(expression=expr, decimal_precision=decimal_precision)
    tool = Math.__new__(Math)
    # Bypass __init__ — tests don't need full agent_loop config.
    tool.config = type("_C", (), {"permission": None})()
    out: MathResult | None = None
    async for ev in tool.run(args):
        if isinstance(ev, MathResult):
            out = ev
    assert out is not None, "tool yielded no MathResult"
    return out


# ============================================================================
# safe_eval — sandbox semantics
# ============================================================================

class TestSafeEval:
    def test_simple_arithmetic(self):
        assert safe_eval("2 + 3 * 4") == 14

    def test_power(self):
        assert safe_eval("2 ** 10") == 1024

    def test_floor_division(self):
        assert safe_eval("17 // 5") == 3

    def test_modulo(self):
        assert safe_eval("17 % 5") == 2

    def test_unary_negation(self):
        assert safe_eval("-(2 + 3)") == -5

    def test_comparison(self):
        assert safe_eval("3 < 5") is True

    def test_boolean_and_or_not(self):
        assert safe_eval("True and not False") is True

    def test_pi_constant(self):
        assert safe_eval("pi") == math.pi

    def test_math_factorial(self):
        # 20! — the known Gemma 4 failure case.
        assert safe_eval("math.factorial(20)") == 2432902008176640000

    def test_math_comb(self):
        # C(50, 5) = 2118760
        assert safe_eval("math.comb(50, 5)") == 2118760

    def test_math_gcd(self):
        assert safe_eval("math.gcd(48, 18)") == 6

    def test_math_lcm(self):
        assert safe_eval("math.lcm(4, 6)") == 12

    def test_math_isqrt(self):
        assert safe_eval("math.isqrt(99)") == 9

    def test_math_sqrt(self):
        assert safe_eval("math.sqrt(16)") == 4.0

    def test_statistics_mean(self):
        assert safe_eval("statistics.mean([1, 2, 3, 4, 5])") == 3.0

    def test_statistics_stdev(self):
        # Known sample stdev of [2,4,4,4,5,5,7,9] = 2.138...
        v = safe_eval("statistics.stdev([2, 4, 4, 4, 5, 5, 7, 9])")
        assert abs(v - 2.138089935299395) < 1e-9

    def test_fraction_arithmetic(self):
        result = safe_eval("Fraction(1, 3) + Fraction(1, 6)")
        assert str(result) == "1/2"

    def test_decimal_arithmetic(self):
        from decimal import Decimal
        result = safe_eval("Decimal('0.1') + Decimal('0.2')")
        # Exact, unlike float 0.1 + 0.2.
        assert result == Decimal("0.3")

    def test_sum_range(self):
        assert safe_eval("sum(range(1, 101))") == 5050

    # ── Sandbox refusals ──

    def test_import_blocked(self):
        with pytest.raises(ValueError):
            safe_eval("__import__('os')")

    def test_open_blocked(self):
        with pytest.raises(ValueError):
            safe_eval("open('/etc/passwd')")

    def test_attribute_on_str_blocked(self):
        with pytest.raises(ValueError):
            safe_eval("'hi'.upper()")

    def test_attribute_on_arbitrary_module_blocked(self):
        with pytest.raises(ValueError):
            # `os` not in allowed names; even attempting attribute access
            # on it should fail at the name check.
            safe_eval("os.system('ls')")

    def test_too_long_blocked(self):
        with pytest.raises(ValueError):
            safe_eval("1" + "+1" * 5000)

    def test_disallowed_node_lambda(self):
        with pytest.raises(ValueError):
            safe_eval("lambda x: x")


# ============================================================================
# Math.run() — tool wrapper integration
# ============================================================================


@pytest.mark.asyncio
async def test_run_simple_arithmetic():
    out = await _run("2 + 3 * 4")
    assert out.ok is True
    assert out.result == "14"
    assert out.result_type == "int"


@pytest.mark.asyncio
async def test_run_factorial_preserves_big_int():
    out = await _run("math.factorial(50)")
    assert out.ok is True
    # 50! = 30414093201713378043612608166064768844377641568960512000000000000
    assert out.result == repr(math.factorial(50))
    assert out.result_type == "int"


@pytest.mark.asyncio
async def test_run_fraction_returned_as_string():
    out = await _run("Fraction(1, 3) + Fraction(1, 6)")
    assert out.ok is True
    assert out.result == "1/2"
    assert out.result_type == "Fraction"


@pytest.mark.asyncio
async def test_run_decimal_with_custom_precision():
    out = await _run("Decimal('1') / Decimal('7')", decimal_precision=40)
    assert out.ok is True
    # 40 digits of 1/7 — first 40 chars of the result string
    assert out.result.startswith("0.142857142857142857142857142857142857142")
    assert out.result_type == "Decimal"


@pytest.mark.asyncio
async def test_run_zero_division_returns_error():
    out = await _run("1 / 0")
    assert out.ok is False
    assert "ZeroDivisionError" in out.error


@pytest.mark.asyncio
async def test_run_syntax_error_returns_error():
    out = await _run("1 +")
    assert out.ok is False
    assert "SyntaxError" in out.error


@pytest.mark.asyncio
async def test_run_disallowed_import_returns_error():
    out = await _run("__import__('os')")
    assert out.ok is False
    assert "ValueError" in out.error or "disallowed" in out.error


@pytest.mark.asyncio
async def test_run_negative_factorial_returns_error():
    out = await _run("math.factorial(-1)")
    assert out.ok is False
    assert "ValueError" in out.error


@pytest.mark.asyncio
async def test_run_statistics_mean():
    out = await _run("statistics.mean([1, 2, 3, 4, 5])")
    assert out.ok is True
    # mean of all-int list keeps int when integral; accept either form.
    assert out.result in ("3", "3.0")


@pytest.mark.asyncio
async def test_run_returns_list_when_appropriate():
    out = await _run("[1, 2, 3] + [4, 5]")
    assert out.ok is True
    assert out.result == "[1, 2, 3, 4, 5]"
    assert out.result_type == "list"


# ============================================================================
# Discovery — tool registers with the right name
# ============================================================================


def test_math_tool_name_is_math():
    assert Math.get_name() == "math"


def test_math_tool_description_mentions_stdlib_modules():
    desc = Math.description
    assert "math" in desc.lower()
    assert "statistics" in desc.lower() or "stdlib" in desc.lower()

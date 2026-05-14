"""Number theory tool — primes, divisors, gcd/lcm, totient, modular arithmetic.

Companion to math (arithmetic), logic (propositional), algebra (CAS).
This tool handles the discrete-math/number-theory operations that HLE
Math frequently leans on but small models get systematically wrong:
- Primality testing for large integers
- Factoring composites
- GCD / LCM
- Euler totient, Möbius, divisor counting / sigma functions
- Modular inverse, modular exponentiation, CRT
- Counting / listing primes in a range

Backend: sympy.ntheory + Python stdlib `math` for gcd/lcm.

Operations (`op=`):

  is_prime(n)                 — True/False, deterministic for any int
  next_prime(n)               — smallest prime > n
  prev_prime(n)               — largest prime < n
  prime_count(a, b)           — π(b) - π(a-1), count primes in [a, b]
  primes_in(a, b)             — list primes in [a, b] (capped at 1000)
  factor(n)                   — prime factorization as a dict
  divisors(n)                 — sorted list of all divisors
  divisor_count(n)            — τ(n), number of divisors
  divisor_sum(n, k=1)         — σ_k(n), sum of k-th powers of divisors
  totient(n)                  — φ(n), Euler totient
  mobius(n)                   — μ(n), Möbius function
  gcd(a, b)                   — greatest common divisor
  lcm(a, b)                   — least common multiple
  mod_inverse(a, m)           — a^(-1) mod m  (raises if not invertible)
  mod_pow(b, e, m)            — b^e mod m  (fast)
  crt(remainders, moduli)     — Chinese Remainder Theorem (comma-separated)

Arguments come in via the `a`, `b`, `m`, `n`, `k`, `expression` fields;
each op uses what it needs. All integer inputs are parsed via sympy so
expressions like `2**100 - 1` and `factorial(20)` work.

Read-only, side-effect free, ALWAYS permission.
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


NumberTheoryOp = Literal[
    "is_prime", "next_prime", "prev_prime", "prime_count", "primes_in",
    "factor", "divisors", "divisor_count", "divisor_sum",
    "totient", "mobius",
    "gcd", "lcm", "mod_inverse", "mod_pow", "crt",
]


class NumberTheoryArgs(BaseModel):
    op: NumberTheoryOp = Field(
        description=(
            "Operation: is_prime | next_prime | prev_prime | prime_count | "
            "primes_in | factor | divisors | divisor_count | divisor_sum | "
            "totient | mobius | gcd | lcm | mod_inverse | mod_pow | crt"
        )
    )
    n: str = Field(
        default="",
        description=(
            "Primary integer (or expression evaluating to one). Used by "
            "single-arg ops: is_prime, next_prime, prev_prime, factor, "
            "divisors, divisor_count, totient, mobius."
        ),
    )
    a: str = Field(default="", description="Left/first arg (gcd, lcm, mod_inverse base, prime_count, primes_in).")
    b: str = Field(default="", description="Right/second arg (gcd, lcm, prime_count, primes_in, mod_pow exponent).")
    m: str = Field(default="", description="Modulus (mod_inverse, mod_pow).")
    e: str = Field(default="", description="Exponent (mod_pow).")
    k: str = Field(default="1", description="Power for divisor_sum (default 1 = standard σ).")
    remainders: str = Field(
        default="",
        description="Comma-separated remainders for crt. Example: '2,3,2'",
    )
    moduli: str = Field(
        default="",
        description="Comma-separated moduli for crt. Example: '3,5,7'",
    )


class NumberTheoryResult(BaseModel):
    ok: bool
    op: str = ""
    result: str = ""
    result_type: str = ""
    error: str = ""


class NumberTheoryConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


# ── Integer expression parser (allows `2**100`, `factorial(20)`, etc.) ─

_BAD_PATTERNS = (
    "__", "import", "exec", "eval", "open",
    "globals", "locals", "getattr", "setattr",
)


def _safe_int_globals() -> dict[str, Any]:
    """Whitelist for parsing integer arguments. Allows arithmetic and a
    handful of sympy/math functions that commonly come up."""
    import math as _math
    import sympy
    return {
        "__builtins__": {},
        "abs": abs, "pow": pow, "min": min, "max": max,
        "factorial": _math.factorial, "comb": _math.comb, "perm": _math.perm,
        "gcd": _math.gcd, "lcm": _math.lcm,
        "Integer": sympy.Integer, "binomial": sympy.binomial,
        "Rational": sympy.Rational,
    }


def _parse_int(expr: str, *, name: str = "value") -> int:
    """Parse `expr` to a Python int. Rejects floats, complex numbers,
    and any expression that doesn't evaluate cleanly to an integer."""
    if not expr or not expr.strip():
        raise ToolError(f"{name} is empty")
    if len(expr) > 200:
        raise ToolError(f"{name} too long")
    lower = expr.lower()
    for bad in _BAD_PATTERNS:
        if bad in lower:
            raise ToolError(f"forbidden token in {name}: {bad!r}")
    try:
        tree = _ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ToolError(f"{name} SyntaxError: {e.msg}") from e
    safe_g = _safe_int_globals()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Attribute):
            raise ToolError(f"{name}: attribute access not permitted")
        if isinstance(node, _ast.Call):
            fn = getattr(node.func, "id", None)
            if fn is None or fn not in safe_g:
                raise ToolError(
                    f"{name}: unknown function {fn!r} "
                    f"(allowed: {sorted(k for k in safe_g if k != '__builtins__')})"
                )
        if isinstance(node, _ast.Name):
            if node.id not in safe_g:
                raise ToolError(f"{name}: undefined name {node.id!r}")
    try:
        value = eval(  # noqa: S307 — whitelisted namespace
            compile(tree, "<nt>", "eval"), safe_g, {}
        )
    except (SyntaxError, ValueError, TypeError, OverflowError) as e:
        raise ToolError(f"{name}: {type(e).__name__}: {e}") from e
    # Accept Python int and sympy.Integer; refuse Rational/Float that
    # would silently truncate. `int(Rational(3,2))` returns 1, which
    # would mask a user error like passing Rational(3,2) where an int
    # was expected.
    try:
        import sympy
        if isinstance(value, sympy.Rational) and not isinstance(value, sympy.Integer):
            raise ToolError(
                f"{name} = {value} is not an integer "
                "(passed Rational/Float; this tool requires Z)"
            )
        as_int = int(value)
        if as_int != value:  # catches floats like 1.5 too
            raise ToolError(
                f"{name} = {value} is not an integer "
                "(this tool requires Z)"
            )
        return as_int
    except (TypeError, ValueError) as e:
        raise ToolError(f"{name} doesn't evaluate to an integer: {e}") from e


def _parse_int_list(s: str, *, name: str) -> list[int]:
    if not s.strip():
        raise ToolError(f"{name} is empty")
    out: list[int] = []
    for piece in s.split(","):
        piece = piece.strip()
        if not piece:
            continue
        out.append(_parse_int(piece, name=f"{name} element"))
    if not out:
        raise ToolError(f"{name}: no integers parsed")
    return out


# ── Op implementations ───────────────────────────────────────────────

def _op_is_prime(args: NumberTheoryArgs) -> tuple[str, str]:
    from sympy import isprime
    return (str(isprime(_parse_int(args.n, name="n"))), "bool")


def _op_next_prime(args: NumberTheoryArgs) -> tuple[str, str]:
    from sympy import nextprime
    return (str(nextprime(_parse_int(args.n, name="n"))), "int")


def _op_prev_prime(args: NumberTheoryArgs) -> tuple[str, str]:
    from sympy import prevprime
    return (str(prevprime(_parse_int(args.n, name="n"))), "int")


def _op_prime_count(args: NumberTheoryArgs) -> tuple[str, str]:
    from sympy import primepi
    a = _parse_int(args.a, name="a")
    b = _parse_int(args.b, name="b")
    if b < a:
        raise ToolError("b must be >= a for prime_count")
    return (str(primepi(b) - primepi(a - 1)), "int")


def _op_primes_in(args: NumberTheoryArgs) -> tuple[str, str]:
    from sympy import primerange
    a = _parse_int(args.a, name="a")
    b = _parse_int(args.b, name="b")
    if b < a:
        raise ToolError("b must be >= a for primes_in")
    primes = list(primerange(a, b + 1))
    if len(primes) > 1000:
        raise ToolError(
            f"primes_in would return {len(primes)} primes; cap is 1000. "
            "Use prime_count for just the count, or narrow the range."
        )
    return (str(primes), "list")


def _op_factor(args: NumberTheoryArgs) -> tuple[str, str]:
    from sympy import factorint
    n = _parse_int(args.n, name="n")
    if n <= 0:
        raise ToolError("factor requires positive n")
    return (str(factorint(n)), "dict")


def _op_divisors(args: NumberTheoryArgs) -> tuple[str, str]:
    from sympy import divisors
    n = _parse_int(args.n, name="n")
    if n <= 0:
        raise ToolError("divisors requires positive n")
    divs = divisors(n)
    if len(divs) > 5000:
        raise ToolError(f"too many divisors ({len(divs)}); cap is 5000")
    return (str(divs), "list")


def _op_divisor_count(args: NumberTheoryArgs) -> tuple[str, str]:
    from sympy import divisor_count
    return (str(divisor_count(_parse_int(args.n, name="n"))), "int")


def _op_divisor_sum(args: NumberTheoryArgs) -> tuple[str, str]:
    from sympy import divisor_sigma
    n = _parse_int(args.n, name="n")
    k = _parse_int(args.k or "1", name="k")
    return (str(divisor_sigma(n, k)), "int")


def _op_totient(args: NumberTheoryArgs) -> tuple[str, str]:
    from sympy import totient
    n = _parse_int(args.n, name="n")
    if n <= 0:
        raise ToolError("totient requires positive n")
    return (str(totient(n)), "int")


def _op_mobius(args: NumberTheoryArgs) -> tuple[str, str]:
    from sympy import mobius
    n = _parse_int(args.n, name="n")
    if n <= 0:
        raise ToolError("mobius requires positive n")
    return (str(mobius(n)), "int")


def _op_gcd(args: NumberTheoryArgs) -> tuple[str, str]:
    import math as _math
    return (str(_math.gcd(_parse_int(args.a, name="a"),
                          _parse_int(args.b, name="b"))), "int")


def _op_lcm(args: NumberTheoryArgs) -> tuple[str, str]:
    import math as _math
    return (str(_math.lcm(_parse_int(args.a, name="a"),
                          _parse_int(args.b, name="b"))), "int")


def _op_mod_inverse(args: NumberTheoryArgs) -> tuple[str, str]:
    from sympy import mod_inverse
    a = _parse_int(args.a, name="a")
    m = _parse_int(args.m, name="m")
    try:
        return (str(mod_inverse(a, m)), "int")
    except ValueError as e:
        # sympy raises ValueError for non-invertible (gcd≠1).
        raise ToolError(f"{a} is not invertible modulo {m}: {e}") from e


def _op_mod_pow(args: NumberTheoryArgs) -> tuple[str, str]:
    # Python's three-arg pow handles huge values fast.
    base = _parse_int(args.b or args.a, name="b (base)")
    e_ = _parse_int(args.e or args.b, name="e (exponent)")
    m = _parse_int(args.m, name="m")
    return (str(pow(base, e_, m)), "int")


def _op_crt(args: NumberTheoryArgs) -> tuple[str, str]:
    from sympy.ntheory.modular import crt
    rs = _parse_int_list(args.remainders, name="remainders")
    ms = _parse_int_list(args.moduli, name="moduli")
    if len(rs) != len(ms):
        raise ToolError(
            f"crt: |remainders|={len(rs)} != |moduli|={len(ms)}"
        )
    result = crt(ms, rs)  # sympy expects (moduli, residues) order
    if result is None:
        raise ToolError("crt: no solution exists (moduli not pairwise compatible)")
    x, M = result
    return (f"x ≡ {x} (mod {M})", "congruence")


_DISPATCH = {
    "is_prime":      _op_is_prime,
    "next_prime":    _op_next_prime,
    "prev_prime":    _op_prev_prime,
    "prime_count":   _op_prime_count,
    "primes_in":     _op_primes_in,
    "factor":        _op_factor,
    "divisors":      _op_divisors,
    "divisor_count": _op_divisor_count,
    "divisor_sum":   _op_divisor_sum,
    "totient":       _op_totient,
    "mobius":        _op_mobius,
    "gcd":           _op_gcd,
    "lcm":           _op_lcm,
    "mod_inverse":   _op_mod_inverse,
    "mod_pow":       _op_mod_pow,
    "crt":           _op_crt,
}


class NumberTheory(
    BaseTool[NumberTheoryArgs, NumberTheoryResult, NumberTheoryConfig, BaseToolState],
    ToolUIData[NumberTheoryArgs, NumberTheoryResult],
):
    description: ClassVar[str] = (
        "Number theory via sympy.ntheory + stdlib — primes, factoring, "
        "divisors, gcd/lcm, Euler totient, Möbius, modular inverse/"
        "exponentiation, Chinese Remainder Theorem. Integer args parse "
        "expressions so `n='2**100 - 1'` and `n='factorial(20)'` work. "
        "Use INSTEAD of guessing primes or trying to factor large "
        "numbers by hand. Sandboxed."
    )

    @classmethod
    def format_call_display(cls, args: NumberTheoryArgs) -> ToolCallDisplay:
        # Build a brief summary
        pieces = []
        for fld in ("n", "a", "b", "m", "e"):
            v = getattr(args, fld)
            if v:
                pieces.append(f"{fld}={v[:20]}")
        summ = " ".join(pieces) or "(no args)"
        return ToolCallDisplay(summary=f"number_theory[{args.op}]: {summ[:50]}")

    @classmethod
    def get_result_display(cls, event: "ToolResultEvent") -> ToolResultDisplay:
        if isinstance(event.result, NumberTheoryResult):
            if not event.result.ok:
                return ToolResultDisplay(
                    success=False, message=f"nt: {event.result.error[:80]}"
                )
            preview = event.result.result
            if len(preview) > 80:
                preview = preview[:77] + "..."
            return ToolResultDisplay(success=True, message=f"= {preview}")
        return ToolResultDisplay(success=True, message="nt complete")

    @classmethod
    def get_status_text(cls) -> str:
        return "Computing"

    def resolve_permission(self, args: NumberTheoryArgs) -> ToolPermission | None:
        return ToolPermission.ALWAYS

    @final
    async def run(
        self, args: NumberTheoryArgs, ctx: "InvokeContext | None" = None
    ) -> AsyncGenerator["ToolStreamEvent | NumberTheoryResult", None]:
        handler = _DISPATCH.get(args.op)
        if handler is None:
            yield NumberTheoryResult(
                ok=False, op=args.op,
                error=f"unknown op {args.op!r}. Allowed: {list(_DISPATCH)}",
            )
            return
        try:
            value, rtype = handler(args)
        except ToolError as e:
            yield NumberTheoryResult(ok=False, op=args.op, error=str(e))
            return
        except Exception as e:  # noqa: BLE001
            yield NumberTheoryResult(
                ok=False, op=args.op, error=f"{type(e).__name__}: {e}"
            )
            return
        yield NumberTheoryResult(ok=True, op=args.op, result=value, result_type=rtype)

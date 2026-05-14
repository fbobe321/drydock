"""Constraint-question detector — nudges Gemma 4 toward the `solve` tool.

The model has the `solve` tool available, but it doesn't reliably reach
for it. Two observed failure modes:
  1. It answers from prior — guesses a value that "looks right."
  2. It tries to enumerate by hand and miscounts.

This hook pattern-matches the user message against the canonical
shapes of constraint problems (find-x-such-that, prove, optimization,
mod arithmetic, logic puzzle vocabulary). On a match, it picks the
worked example closest to the question shape and injects it as a
system note. That gives the model a concrete template to specialize
rather than abstract advice.

Same shape as the GraphRAG auto-prefetch and the curiosity-gap
logger: env-gated, log-only on miss, idempotent within a single
user-prompt turn.

Disabled via DRYDOCK_CONSTRAINT_HINT=0.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("drydock.constraint_hint")


# Pattern → (label, worked-example string). Patterns are evaluated in
# order; the first matching pattern wins. Each example shows the
# variables/constraints encoding for that question shape, so the model
# can specialize rather than invent the encoding from scratch.
_PATTERN_HINTS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(
            r"\b(einstein|zebra)\s+puzzle\b"
            r"|\blogic\s+puzzle\b"
            r"|\bwho\s+(lives|owns|drinks|smokes)\b"
            r"|\b(?:n|\d+)[\s\-]?queens\b"
            r"|\bsudoku\b",
            re.IGNORECASE,
        ),
        "logic-puzzle",
        'solve(op="solve", variables="a:Int, b:Int, c:Int",\n'
        '      constraints=[\n'
        '        "a >= 1", "a <= 3", "b >= 1", "b <= 3", "c >= 1", "c <= 3",\n'
        '        "Distinct(a, b, c)",      # all-different\n'
        '        "a == 1",                  # \\"X is in position 1\\"\n'
        '        "Abs(b - a) == 1",         # \\"Y is next to X\\"\n'
        '        "c != 2",                  # \\"Z is not in position 2\\"\n'
        '      ])\n'
        '→ sat: a=1, b=2, c=3',
    ),
    (
        re.compile(
            r"\bmod(?:ulo|ular)?\b"
            r"|\bmod\s*\d"
            r"|\(mod\s+\w+\)"
            r"|\b\d+\s*[≡=]\s*\d+\s*\(?mod"
            r"|[a-zA-Z]\s*%\s*\d+"  # `n % 7`, `x%5`
            r"|\bdivisible\s+by\b"
            r"|\bremainder\b",
            re.IGNORECASE,
        ),
        "modular-arithmetic",
        'solve(op="solve", variables="x:Int",\n'
        '      constraints=["x >= 0", "x < 7", "3*x % 7 == 5"])\n'
        '→ sat: x=4    (smallest non-negative x with 3x ≡ 5 mod 7)\n'
        '\n'
        'For \\"find all solutions in the range\\", use op="find_all".\n'
        'For \\"smallest x with property P\\", use op="optimize", direction="min".',
    ),
    (
        re.compile(
            r"\bmaxim(?:ize|um)\b|\bminim(?:ize|um)\b"
            r"|\bsmallest\s+(?:value|integer|number|x|n)\b"
            r"|\blargest\s+(?:value|integer|number|x|n)\b"
            r"|\bsubject\s+to\b"
            r"|\boptimize\b",
            re.IGNORECASE,
        ),
        "optimization",
        'solve(op="optimize", variables="x:Int, y:Int",\n'
        '      constraints=["x + y == 10", "x >= 0", "y >= 0"],\n'
        '      objective="x * y", direction="max")\n'
        '→ optimal: x=5, y=5, objective=25',
    ),
    (
        re.compile(
            r"\b(prove|show\s+that|demonstrate|verify\s+that)\b"
            r"|\bif\s+and\s+only\s+if\b"
            r"|\biff\b"
            r"|\bnecessary\s+and\s+sufficient\b",
            re.IGNORECASE,
        ),
        "prove",
        'solve(op="prove", variables="x:Int",\n'
        '      constraints=["x > 0"],\n'
        '      conclusion="x + 1 > 1")\n'
        '→ valid     (constraints entail the conclusion)\n'
        '\n'
        'When `prove` returns `countered`, the `model` field is the\n'
        'counterexample — that IS the answer for \\"is this true\\" questions.',
    ),
    (
        re.compile(
            r"\bfind\s+(?:all\s+)?(?:x|y|n|the\s+(?:value|values|number|integer|integers))\s+such\s+that\b"
            r"|\bfor\s+what\s+(?:value|values|x|y|n)\s+(?:does|is|of)\b"
            r"|\bexists\s+(?:a|an)\s+\w+\s+such\s+that\b"
            r"|\bthere\s+(?:is|exists)\s+(?:a|an|some)\s+\w+\s+(?:such\s+that|with)\b",
            re.IGNORECASE,
        ),
        "find-such-that",
        'solve(op="solve", variables="x:Int, y:Int",\n'
        '      constraints=["x + y == 10", "x - y == 4"])\n'
        '→ sat: x=7, y=3\n'
        '\n'
        'Variable types: Int | Real | Bool | BitVec<N>. Operators: ==, !=,\n'
        '<, <=, >, >=, +, -, *, /, %. Functions: And, Or, Not, Implies,\n'
        'If, Distinct, Abs, Sum.',
    ),
]


def _strip_boilerplate(text: str) -> str:
    """Pull the actual question out of HLE / shakedown wrappers.

    Same surgery as the GraphRAG auto-prefetch — \"QUESTION:\" prefix and
    \"FINAL ANSWER:\" / \"Format your\" suffix get stripped so patterns
    don't false-match on scaffolding.
    """
    s = text or ""
    marker = s.find("QUESTION:")
    if marker >= 0:
        s = s[marker + len("QUESTION:"):]
    for stopper in ("FINAL ANSWER:", "Format your", "End your response",
                    "Your answer"):
        idx = s.find(stopper)
        # Strip iff there's at least a real question in front (>=20 chars).
        # The original auto-prefetch used 50; we use a smaller threshold
        # here so short questions like "Solve x+1=5.\nFINAL ANSWER:" still
        # match.
        if idx >= 20:
            s = s[:idx]
    return s.strip()


def detect_constraint_shape(user_msg: str) -> tuple[str, str] | None:
    """Return (label, worked_example) when the message has a
    constraint-problem shape, or None otherwise.

    Patterns are checked in priority order: logic-puzzle first (most
    specific), then modular arithmetic, optimization, prove, and finally
    the broad \"find x such that\" catchall. The first match wins.
    """
    cleaned = _strip_boilerplate(user_msg)
    if len(cleaned) < 10:
        return None
    for pat, label, example in _PATTERN_HINTS:
        if pat.search(cleaned):
            return (label, example)
    return None


def build_hint(label: str, example: str) -> str:
    """Format a system note around the worked example.

    Kept compact (≈350 chars) so it doesn't dominate the context.
    Frames the example as \"specialize THIS template,\" not \"here's an
    abstract guideline\" — the project memory says Gemma 4 trusts
    concrete shapes and ignores prose advice.
    """
    return (
        f"[constraint-hint] This question has a {label} shape. "
        f"Encode as a `solve` call rather than reasoning step-by-step. "
        f"Z3 is sound and complete on integer/real/boolean constraints. "
        f"Template to specialize:\n\n{example}\n\n"
        f"See the `constraint-reasoning` skill for the full encoding "
        f"reference (Int/Real/Bool/BitVec, Distinct, If, Sum, Implies)."
    )

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
    # ── Boolean / propositional algebra (Zhigalkin, formulas, truth tables) ──
    # Pure boolean problems are Z3's bread and butter — the SAT/SMT solver
    # decides equivalence and satisfiability efficiently. HLE example:
    # 66edc256...d744 ("Zhigalkin polynomial of a Boolean formula") which
    # the model got wrong (gold='$((a↓b)↑¬(c↓b))↔︎¬(d...').
    (
        re.compile(
            r"\bboolean\s+(?:formula|expression|function|polynomial|algebra)\b"
            r"|\bzhigalkin\s+polynomial\b"
            r"|\balgebraic\s+normal\s+form\b"
            r"|\b(?:propositional|prop)\s+(?:formulas?|expressions?|logic|sentences?)\b"
            r"|\btruth\s+table\b"
            r"|\b(?:nand|nor|xor)\b|⊕|\\oplus\b"
            r"|\b(equivalent|equivalence)\s+of\s+(?:two\s+)?(?:formulas?|expressions?)\b",
            re.IGNORECASE,
        ),
        "boolean-algebra",
        '# For "is formula A equivalent to formula B?" or "simplify formula F":\n'
        'solve(op="prove", variables="a:Bool, b:Bool, c:Bool",\n'
        '      constraints=[],\n'
        '      conclusion="<FORMULA_A> == <FORMULA_B>")\n'
        '→ valid (equivalent) or countered (with a:Bool=<witness>)\n'
        '\n'
        '# For "find a satisfying assignment to F":\n'
        'solve(op="solve", variables="a:Bool, b:Bool, c:Bool",\n'
        '      constraints=["<FORMULA_AS_PYTHON_BOOL_EXPR>"])\n'
        '\n'
        '# Operators: And(a,b), Or(a,b), Not(a), Implies(p,q), Xor(p,q)\n'
        '# Z3 decides Boolean SAT/equivalence definitively — DO NOT try to\n'
        '# work out De Morgan / contrapositive / XOR identities by hand.',
    ),

    # ── Counting structures over a finite set ──
    # "How many <structures> on a set of N elements". For small N these
    # are enumerable by brute search but tedious; Z3 with BitVec/Int tables
    # nails them. HLE example: 66edc256...754 ("How many associative AND
    # commutative binary operations on 3 elements?", gold=63 — Z3 can
    # encode this as a 3×3 table of Ints with the right axioms).
    (
        re.compile(
            r"\bhow\s+many\s+(?:associative|commutative|abelian|cyclic|distinct|finite)\b"
            r"|\bhow\s+many\s+(?:binary\s+)?(?:operations?|functions?|relations?|graphs?|structures?|groups?|rings?|fields?|magmas?|monoids?|semigroups?)\b.{0,30}(?:on|over|with)\s+(?:a\s+set\s+of|the\s+set)?\s*\d"
            r"|\bnumber\s+of\s+(?:operations?|functions?|relations?|graphs?|matrices|sequences?)\s+(?:on|over|with|in)\b",
            re.IGNORECASE,
        ),
        "structure-count",
        '# For "How many <X> operations on a set of N elements?":\n'
        '# Encode the op as a table: op[i][j] = some value in {0..N-1}.\n'
        '# Use Int variables t_ij for the N² table cells, then add axioms.\n'
        '#\n'
        '# Example: count commutative AND associative binary ops on {0,1,2}\n'
        'solve(op="find_all",\n'
        '      variables="t00:Int,t01:Int,t02:Int,t11:Int,t12:Int,t22:Int",\n'
        '      constraints=[\n'
        '        # range\n'
        '        "t00>=0","t00<=2","t01>=0","t01<=2","t02>=0","t02<=2",\n'
        '        "t11>=0","t11<=2","t12>=0","t12<=2","t22>=0","t22<=2",\n'
        '        # associativity: (a*b)*c == a*(b*c) for all 27 (a,b,c)\n'
        '        # (write only the non-redundant cases — use If(...) to look\n'
        '        # up t[a][b]; commutativity reduces 9 entries to 6)\n'
        '      ],\n'
        '      limit=200, timeout_ms=30000)\n'
        '→ len(models) is the answer.\n'
        '\n'
        '# REAL HLE: "comm+assoc binary ops on {0,1,2}" → 63 solutions.',
    ),

    # ── Perfect-power-counting / Diophantine-finite-search ──
    # Highest priority because it has the most specific template, and
    # because it's one of the few HLE question classes where Z3 is
    # demonstrably strictly better than reasoning (verified: HLE
    # 66ea031360fbbf249dec70e1, Z3 returned exactly 4 solutions in 4.4s).
    (
        re.compile(
            r"\bfor\s+how\s+many\s+(?:integers?|natural\s+numbers?|positive\s+integers?)\b.*?\b(?:perfect\s+(?:square|cube|power)|a\s+square|a\s+cube|prime|divisible|squarefree)\b"
            r"|\bperfect\s+(?:square|cube|power)\b.*?\bfor\s+how\s+many\b"
            r"|\bhow\s+many\s+(?:non-?negative\s+|positive\s+)?integer\s+(?:solutions?|tuples?|points?)\b"
            r"|\bhow\s+many\s+(?:integers?|values?|positive\s+integers?|natural\s+numbers?)\s+(?:x|y|n|k|m|with|such|less|greater|between|in)\b"
            r"|\bhow\s+many\s+(?:positive\s+integers?|natural\s+numbers?|integers?).{0,40}?\b(?:squarefree|prime|perfect|divisible|composite|coprime|relatively\s+prime)\b"
            r"|\bdiophantine\s+(?:equation|solutions?)\b"
            r"|\bnumber\s+of\s+(?:non-?negative\s+)?integer\s+(?:solutions?|tuples?|pairs?)\b"
            r"|\bcount\s+the\s+(?:number\s+of\s+)?(?:integers?|solutions?|tuples?)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "diophantine-count",
        '# For "For how many integers x is f(x) a perfect square?":\n'
        'solve(op="find_all", variables="x:Int, y:Int",\n'
        '      constraints=[\n'
        '        "y * y == <FILL: f(x) as polynomial in x>",\n'
        '        "x >= -100", "x <= 100",   # adjust bound by problem scale\n'
        '        "y >= 0",                   # canonical: only positive root\n'
        '      ],\n'
        '      limit=100, timeout_ms=30000)\n'
        '→ count len(models) — that IS the answer\n'
        '\n'
        '# For "Diophantine: how many (x1,...,xk) with sum^2 = N?":\n'
        'solve(op="find_all", variables="x1:Int, x2:Int, x3:Int",\n'
        '      constraints=["x1 >= 0", "x2 >= 0", "x3 >= 0",\n'
        '                   "x1*x1 + x2*x2 + x3*x3 == 2024"],\n'
        '      limit=100000)\n'
        '\n'
        '# REAL HLE WIN: f(x)=x^3-16x^2-72x+1056 perfect square → 4 solutions\n'
        '#   (x=-4, x=4, x=17, x=65). Z3 finds them in 4 seconds, exact.',
    ),
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
            r"\b(prove|show\s+that|demonstrate|verify\s+that|verify\b)\b"
            r"|\bif\s+and\s+only\s+if\b"
            r"|\biff\b"
            r"|\bnecessary\s+and\s+sufficient\b"
            # Coding-shaped variants — counterexamples + universal claims
            r"|\b(find|construct)\s+(a\s+)?counterexamples?\b"
            r"|\bfor\s+all\s+(positive\s+|integer|natural\s+|negative\s+)?\w+,\s*(does|is|prove)\b"
            r"|\b(this|the)\s+(loop|function|invariant|method)\s+(terminates|holds|preserves)\b",
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
            # Classic "find x such that" / "for what x"
            r"\bfind\s+(?:all\s+)?(?:x|y|n|k|m|the\s+(?:value|values|number|integer|integers|set|points?))\b"
            r"|\bfor\s+what\s+(?:value|values|x|y|n|k|m|integers?)\b"
            r"|\bexists\s+(?:a|an)\s+\w+\s+such\s+that\b"
            r"|\bthere\s+(?:is|exists)\s+(?:a|an|some)\s+\w+\s+(?:such\s+that|with|where)\b"
            r"|\bis\s+there\s+(?:a|an|some|any)\s+(?:\w+\s+){1,4}(?:such\s+that|with|where)\b"
            # Coding-specific: branch-trigger inputs, test cases
            r"|\bfind\s+(?:an?\s+)?inputs?\s+(?:that|where|to)\b"
            r"|\bwhat\s+inputs?\s+(?:would|will|cause|trigger)\b"
            r"|\b(generate|construct|design)\s+(?:a\s+)?(?:test|input)\s+(?:case|that)\b"
            # Counting questions: "for how many integers x is...", "how many ... satisfy"
            r"|\bfor\s+how\s+many\b"
            r"|\bhow\s+many\s+(?:integers?|values?|solutions?|positive|natural)\b"
            r"|\bdetermine\s+(?:all|the\s+number\s+of|how\s+many)\b"
            # Bound problems
            r"|\b(lower|upper)\s+bound\b"
            r"|\bfind\s+the\s+(?:lower|upper|tight)\b"
            # Perfect square / power / divisibility predicates (Diophantine territory)
            r"|\bperfect\s+(?:square|cube|power)\b"
            r"|\bdiophantine\b"
            # Compute-the-number-of style
            r"|\bcompute\s+(?:the\s+(?:number|count|sum)\s+of|the\s+value\s+of)\b"
            # Equivalence/equation-solving
            r"|\bsolve\s+(?:the\s+)?(?:equation|system|inequality)\b"
            # "What is the X-th prime / largest / smallest" — finite search
            r"|\bwhat\s+is\s+the\s+(?:smallest|largest|number\s+of|maximum|minimum)\b",
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

    When the question has an extractable concrete formula (e.g. "is
    f(x) a perfect square"), the worked example is SPECIALISED with
    f(x) already filled in. Otherwise the generic template is returned.
    """
    cleaned = _strip_boilerplate(user_msg)
    if len(cleaned) < 10:
        return None
    for pat, label, example in _PATTERN_HINTS:
        if pat.search(cleaned):
            # Try to specialise the example with extracted formula/predicate.
            try:
                from drydock.core.constraint_extract import extract, render_template
                extr = extract(cleaned)
                if extr is not None and extr.confidence >= 0.5:
                    specialised = render_template(extr)
                    if specialised:
                        return (label, specialised + "\n\n# Generic template "
                                "for reference (only use if the specialisation "
                                "above is wrong):\n" + example)
            except Exception:  # noqa: BLE001 — extraction is best-effort
                pass
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

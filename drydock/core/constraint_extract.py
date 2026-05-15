"""Constraint-extractor — pull the concrete polynomial / predicate /
bounds out of an HLE-style question, so the constraint-hint template
arrives PRE-FILLED instead of as a generic skeleton.

The gap this closes: the detector matches the shape, but the worked
example it injects is generic ("y*y == <FILL>"). Gemma 4 then has to
read the question, translate LaTeX to Python, and fill in the blank.
Empirically (HLE first night of v2.8.31, 0/80 sessions called solve)
it doesn't make that leap.

This module does the translation FOR the model:

  Input:  "For how many integers x is x^3 - 16x^2 - 72x + 1056
           a perfect square?"
  Output: predicate=perfect_square,
          formula="x*x*x - 16*x*x - 72*x + 1056",
          variables=["x"]

The constraint_hint module then renders the template with formula
pre-filled, so the model sees a ready-to-call solve(...) instead of
a placeholder.

Why a hand-rolled extractor instead of a tiny LLM call:
- It runs on every user message; an LLM call would add 1-3s latency
- Deterministic — no risk of hallucinated coefficients
- Easy to test and version

Scope limit: this only handles the shapes the constraint detector
catches AND that Z3 can plausibly solve. Anything outside that is
returned as `None` and the caller falls back to the generic template.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


PredicateKind = Literal[
    "perfect_square",     # is f(x) a perfect square?
    "perfect_cube",
    "prime",              # is f(x) prime?
    "divisible_by",       # is f(x) divisible by k?
    "equals_constant",    # f(x) == c (Diophantine)
    "boolean_equiv",      # is formula A equivalent to formula B?
]


@dataclass
class ExtractResult:
    """Concrete parsed form of a constraint-shaped question.

    Caller renders this into a Z3 template with the formula already
    in place. `confidence` lets us fall back to the generic template
    when extraction is too uncertain — better to give the model a
    truthful generic skeleton than a confidently-wrong specialised one.
    """
    predicate: PredicateKind
    formula: str                    # Python-Z3 ready, e.g. "x*x*x - 16*x*x"
    variables: list[str] = field(default_factory=list)  # e.g. ["x"]
    divisor: int | None = None      # for predicate == "divisible_by"
    target: str | None = None       # for equals_constant
    second_formula: str | None = None  # for boolean_equiv
    confidence: float = 1.0         # 0..1; below 0.5 the caller should
                                    # fall back to generic template
    raw_match: str = ""             # the snippet we extracted from


# ── Cleaning helpers ────────────────────────────────────────────────────

# LaTeX commands we want to strip silently. Anything left after these is
# treated as either a variable, an operator, or a constant.
_LATEX_DROP_PATTERNS = [
    r'\\in\s+\\mathbb\{[A-Z]\}',
    r'\\mathbb\{[A-Z]\}',
    r'\\mathbb',
    r'\\left|\\right',
    r'\\(?:displaystyle|textstyle|operatorname)\b',
    r'\\cdot\s*',
    r'\\quad|\\,|\\;|\\:|\\!',
]

# Map common LaTeX to ASCII / Python equivalents
_LATEX_REPLACE = [
    (r'\\geq\b', '>='),
    (r'\\leq\b', '<='),
    (r'\\neq\b', '!='),
    (r'\\le\b', '<='),
    (r'\\ge\b', '>='),
    (r'\\equiv\b', '=='),
    (r'\\times\b', '*'),
    (r'\\div\b', '/'),
    (r'\\pm\b', '+/-'),
    (r'\\mod\b', '%'),
]


def _clean_latex(s: str) -> str:
    """Best-effort LaTeX → plain Python expression."""
    # Strip $...$ delimiters (display math) — leave content
    s = re.sub(r'\$\$?([^$]+)\$\$?', r'\1', s)
    # Drop the commands we never want to see
    for pat in _LATEX_DROP_PATTERNS:
        s = re.sub(pat, ' ', s)
    # Replace structured commands
    for pat, repl in _LATEX_REPLACE:
        s = re.sub(pat, repl, s)
    # Powers: x^2 → x**2, x^{2} → x**2
    s = re.sub(r'\^\{([^}]+)\}', lambda m: '**(' + m.group(1).strip() + ')', s)
    s = re.sub(r'\^(\w+)', r'**\1', s)
    # \frac{a}{b} → (a)/(b)
    s = re.sub(r'\\frac\{([^}]+)\}\{([^}]+)\}', r'(\1)/(\2)', s)
    # Strip remaining \word commands
    s = re.sub(r'\\[a-zA-Z]+\*?', ' ', s)
    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _normalize_for_z3(formula: str) -> str:
    """Make a cleaned expression Z3-ready.

    Z3 (via our solve tool) reads Python syntax with explicit operators.
    `16x**2` must become `16*x**2`. `x**3` is fine. Trailing/leading
    operators get trimmed.
    """
    s = formula.strip()
    # Insert * between coefficient and variable: 16x → 16*x, 5x_1 → 5*x_1
    s = re.sub(r'(\d)([a-zA-Z_])', r'\1*\2', s)
    # Insert * between )( or )variable: )(x → )*(x , 2(x → 2*(x
    s = re.sub(r'\)(\s*)\(', r')\1*(', s)
    s = re.sub(r'(\d)\(', r'\1*(', s)
    s = re.sub(r'([a-zA-Z_])\s*\(', lambda m: (
        m.group(0) if m.group(1).lower() in ('and', 'or', 'not', 'if', 'sum',
                                              'abs', 'distinct', 'implies',
                                              'xor', 'min', 'max')
        else m.group(1) + '*('
    ), s)
    # Replace ** with explicit multiplication for small powers — keeps
    # solve_tool happy without depending on Python's ** parsing through
    # the AST validator. x**3 → x*x*x. Skip if exponent is >= 6 (avoid
    # huge expressions) or non-integer.
    def _expand_pow(m: re.Match) -> str:
        var = m.group(1)
        try:
            n = int(m.group(2))
        except ValueError:
            return m.group(0)
        if 2 <= n <= 6:
            return "*".join([var] * n)
        return m.group(0)
    s = re.sub(r'([A-Za-z_][A-Za-z0-9_]*)\*\*(\d+)', _expand_pow, s)
    # Clean stray spaces around operators. Treat `**` as a single token
    # so we don't split power operators into ` * * `.
    s = re.sub(r'\s*(\*\*|[+\-*/=<>])\s*', r' \1 ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


# ── Predicate extractors ────────────────────────────────────────────────

# "For how many integers x is f(x) a perfect square?"
# "is the quantity F a perfect square"
_RE_PERFECT_SQUARE = re.compile(
    r'(?:is\s+(?:the\s+(?:quantity|expression|value|number)\s+)?'
    r'|(?:satisfies?|with)\s+)'
    r'(?P<f>[\w\s\^\{\}+\-*/().\\]+?)'
    r'\s+(?:is\s+)?a\s+perfect\s+square',
    re.IGNORECASE | re.DOTALL,
)
_RE_PERFECT_CUBE = re.compile(
    r'(?:is\s+(?:the\s+(?:quantity|expression|value|number)\s+)?)'
    r'(?P<f>[\w\s\^\{\}+\-*/().\\]+?)'
    r'\s+(?:is\s+)?a\s+perfect\s+cube',
    re.IGNORECASE | re.DOTALL,
)
# "is f(x) prime" or "f(x) is prime" or "such that f(x) is prime"
_RE_PRIME = re.compile(
    r'(?:\bis\s+|\bsuch\s+that\s+)(?P<f>[\w\s\^\{\}+\-*/().\\]{3,200}?)\s+(?:is\s+)?prime\b',
    re.IGNORECASE | re.DOTALL,
)
# "f(x) is divisible by k". Capture permissively; `_truncate_formula_after_predicate`
# extracts the tail math-only substring.
_RE_DIVISIBLE = re.compile(
    r'(?P<f>[\w\s\^\{\}+\-*/().\\]{3,200}?)'
    r'\s+is\s+divisible\s+by\s+(?P<k>\d+)',
    re.IGNORECASE | re.DOTALL,
)
# Diophantine "x1^2 + x2^2 + ... == N"
_RE_EQUALS_CONSTANT = re.compile(
    r'(?P<f>[\w\^\{\}+\-*/().\s\\]+?)\s*=\s*(?P<c>\d{2,})\b',
    re.IGNORECASE | re.DOTALL,
)


def _extract_variables(formula: str) -> list[str]:
    """Pull single-letter and subscripted variable names from a
    normalised formula. Skips known math constants and operators."""
    SKIP = {'and', 'or', 'not', 'if', 'sum', 'abs', 'distinct', 'implies',
            'xor', 'min', 'max', 'true', 'false', 'pi', 'e', 'oo'}
    seen: list[str] = []
    for tok in re.finditer(r'\b([A-Za-z_][A-Za-z0-9_]*)\b', formula):
        n = tok.group(1)
        if n.lower() in SKIP:
            continue
        if n.isdigit():
            continue
        if n not in seen:
            seen.append(n)
    return seen


_STOP_WORDS = frozenset({
    "satisfy", "satisfies", "with", "where", "such", "that",
    "is", "are", "be", "the", "quantity", "expression", "value", "number",
    "in", "integer", "integers", "positive", "negative", "non",
    "natural", "any", "for", "how", "many", "exists", "exist",
    "does", "do", "an", "a", "let", "consider",
})


def _truncate_formula_after_predicate(f: str) -> str:
    """Trim everything up to the LAST stop word in the capture, leaving
    only the math expression that follows.

    Examples:
        "How many integers x satisfy x**2 + 1"  → "x**2 + 1"
        "the quantity x**3 - 16x**2 - 72x + 1056" → "x**3 - 16x**2 - 72x + 1056"
        "for how many integers x is x**2 + 1"  → "x**2 + 1"
        "How many n such that 2**n - 1"        → "2**n - 1"

    Algorithm: scan tokens left-to-right; record the position after
    the LAST stop word; take everything after that position.
    """
    s = f.strip()
    tokens = re.findall(r'\w+|[^\w\s]+|\s+', s)
    last_stop_idx = -1
    for i, tok in enumerate(tokens):
        if tok.strip().lower() in _STOP_WORDS:
            last_stop_idx = i
    if last_stop_idx >= 0 and last_stop_idx < len(tokens) - 1:
        tail = "".join(tokens[last_stop_idx + 1:]).strip()
        # Sanity: the tail must contain at least one operator (otherwise
        # we stripped too far and left a single var).
        if re.search(r'[+\-*/^]', tail):
            return tail
    return s.strip()


# ── Public API ──────────────────────────────────────────────────────────

def extract(question: str) -> ExtractResult | None:
    """Best-effort extract of a Z3-ready predicate + formula.

    Returns None if no high-confidence match is found. The caller (the
    constraint_hint module) should fall back to the generic template
    in that case.
    """
    if not question:
        return None
    cleaned = _clean_latex(question)

    # Try predicates in specificity order
    for kind, pat in [
        ("perfect_square", _RE_PERFECT_SQUARE),
        ("perfect_cube",   _RE_PERFECT_CUBE),
        ("prime",          _RE_PRIME),
        ("divisible_by",   _RE_DIVISIBLE),
    ]:
        m = pat.search(cleaned)
        if not m:
            continue
        f_raw = _truncate_formula_after_predicate(m.group("f"))
        # Reject if the captured formula is too short / lacks an operator
        # (probably picked up just "n" or a stray word).
        if len(f_raw) < 3 or not re.search(r'[+\-*^/]', f_raw):
            continue
        f_norm = _normalize_for_z3(f_raw)
        vs = _extract_variables(f_norm)
        if not vs:
            continue
        result = ExtractResult(
            predicate=kind,
            formula=f_norm,
            variables=vs,
            raw_match=m.group(0)[:200],
        )
        if kind == "divisible_by":
            try:
                result.divisor = int(m.group("k"))
            except (ValueError, IndexError):
                continue
        # Confidence drops if the formula has weird LaTeX residue
        if re.search(r'[{}\\]', f_norm):
            result.confidence = 0.4
        return result

    return None


def render_template(extr: ExtractResult) -> str:
    """Generate a fully-specialised solve(...) call from the extracted
    predicate + formula. Returned as a multi-line Python-ish string
    ready to drop into a system note.
    """
    vars_csv = ", ".join(f"{v}:Int" for v in extr.variables)
    main_var = extr.variables[0] if extr.variables else "x"

    if extr.predicate == "perfect_square":
        return (
            f'solve(op="find_all",\n'
            f'      variables="{vars_csv}, y:Int",\n'
            f'      constraints=[\n'
            f'        "y * y == {extr.formula}",\n'
            f'        "{main_var} >= -200", "{main_var} <= 200",  '
            f'# adjust if expected magnitude differs\n'
            f'        "y >= 0",\n'
            f'      ],\n'
            f'      limit=100, timeout_ms=30000)\n'
            f'→ len(models) is the answer (count of integer solutions).'
        )
    if extr.predicate == "perfect_cube":
        return (
            f'solve(op="find_all",\n'
            f'      variables="{vars_csv}, y:Int",\n'
            f'      constraints=[\n'
            f'        "y * y * y == {extr.formula}",\n'
            f'        "{main_var} >= -200", "{main_var} <= 200",\n'
            f'      ],\n'
            f'      limit=100, timeout_ms=30000)'
        )
    if extr.predicate == "prime":
        # Z3 can't check primality directly; instruct the model to use the
        # number_theory tool for each candidate, but still help with the
        # range search via solve when the predicate is reframable.
        return (
            f'# For "is f(n) prime?" — Z3 alone CANNOT check primality.\n'
            f'# Use solve to enumerate small n values, then number_theory.is_prime:\n'
            f'solve(op="find_all", variables="{vars_csv}",\n'
            f'      constraints=[\n'
            f'        "{main_var} >= 1", "{main_var} <= 100",  # search range\n'
            f'      ], limit=100)\n'
            f'# For each n in the result, call:\n'
            f'#   number_theory(op="is_prime", n="<value of {extr.formula}>")\n'
            f'# Count those where is_prime returns True.'
        )
    if extr.predicate == "divisible_by":
        return (
            f'solve(op="find_all", variables="{vars_csv}",\n'
            f'      constraints=[\n'
            f'        "({extr.formula}) % {extr.divisor} == 0",\n'
            f'        "{main_var} >= 1", "{main_var} <= 100",  '
            f'# adjust range to problem\n'
            f'      ], limit=100)\n'
            f'→ len(models) is the count of solutions in the range.'
        )
    return ""

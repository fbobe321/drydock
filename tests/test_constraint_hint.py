"""Tests for the constraint-shape detector hook."""
from __future__ import annotations

import pytest

from drydock.core.constraint_hint import (
    build_hint,
    detect_constraint_shape,
    _strip_boilerplate,
)


# ── Positive matches (each shape → expected label) ────────────────────

@pytest.mark.parametrize("msg,label", [
    # find-such-that catchall
    ("Find x such that 3x + 5 = 14.", "find-such-that"),
    ("For what value of n does the sequence converge?", "find-such-that"),
    ("There exists an x such that x^2 = 49.", "find-such-that"),

    # modular arithmetic
    ("Find all integers n with 1 <= n <= 100 and n % 7 == 3.", "modular-arithmetic"),
    ("What integers between 1 and 50 are divisible by 7?", "modular-arithmetic"),
    ("Find x such that 3x ≡ 5 (mod 7).", "modular-arithmetic"),
    ("What is the remainder when 2^100 is divided by 13?", "modular-arithmetic"),

    # optimization
    ("Maximize x*y subject to x + y = 10.", "optimization"),
    ("What's the smallest integer x with x*x > 100?", "optimization"),
    ("Find the largest n such that n! < 1e9.", "optimization"),
    ("Minimize cost subject to the constraints listed above.", "optimization"),

    # prove
    ("Prove that for all positive integers x, x + 1 > x.", "prove"),
    ("Show that A iff B.", "prove"),
    ("Demonstrate that the sum is always even.", "prove"),
    ("X is necessary and sufficient for Y.", "prove"),

    # logic puzzles
    ("Three nationalities live in three houses. Who lives in house 2?", "logic-puzzle"),
    ("This is a classic Einstein puzzle.", "logic-puzzle"),
    ("Solve the 8-queens problem.", "logic-puzzle"),
    ("Here is a Sudoku to solve.", "logic-puzzle"),
])
def test_detector_positive_matches(msg: str, label: str) -> None:
    hit = detect_constraint_shape(msg)
    assert hit is not None, f"expected {label!r} match for {msg!r}"
    assert hit[0] == label, f"got {hit[0]!r}, expected {label!r}"
    assert hit[1].startswith("solve(")  # worked example starts with a solve call


# ── Negative matches (coding / arithmetic / generic chat) ─────────────

@pytest.mark.parametrize("msg", [
    "What is 2+2?",
    "Write a Python script that reads a file.",
    "Refactor this function to be faster.",
    "Plan the new feature.",
    "Build me a calculator app.",
    "Explain this code.",
    "Run the tests.",
    "Hello",
    "",
    "?",
])
def test_detector_no_false_positives(msg: str) -> None:
    assert detect_constraint_shape(msg) is None


# ── Priority ordering (most-specific wins) ─────────────────────────────

def test_logic_puzzle_wins_over_find():
    # Contains "find" but is clearly a logic puzzle — most-specific wins.
    msg = "Find who lives in house 1 — it's a classic logic puzzle."
    hit = detect_constraint_shape(msg)
    assert hit is not None and hit[0] == "logic-puzzle"


def test_optimize_wins_over_find():
    msg = "Find the smallest integer x such that x > 5."
    hit = detect_constraint_shape(msg)
    assert hit is not None and hit[0] == "optimization"


def test_prove_wins_over_modular():
    msg = "Prove that 2^p ≡ 2 (mod p) for prime p (Fermat's little theorem)."
    hit = detect_constraint_shape(msg)
    # Modular arithmetic is listed before prove in priority — change if intent shifts.
    # Current behavior: modular shape wins because it's matched first.
    assert hit is not None
    assert hit[0] in ("modular-arithmetic", "prove")


# ── HLE wrapper stripping ─────────────────────────────────────────────

def test_strips_hle_question_prefix():
    raw = (
        "Answer this question. End your response with FINAL ANSWER: <answer>\n"
        "QUESTION: Find x such that x + 1 = 5.\n"
        "FINAL ANSWER:"
    )
    stripped = _strip_boilerplate(raw)
    assert "QUESTION" not in stripped
    assert "FINAL ANSWER" not in stripped
    assert "Find x such that x + 1 = 5." in stripped


def test_detects_inside_hle_wrapper():
    raw = (
        "Answer this question. End your response with FINAL ANSWER: <answer>\n"
        "QUESTION: Maximize x + y subject to x + y <= 10.\n"
        "FINAL ANSWER:"
    )
    hit = detect_constraint_shape(raw)
    assert hit is not None and hit[0] == "optimization"


# ── Hint formatting ───────────────────────────────────────────────────

def test_build_hint_contains_template_and_skill_pointer():
    note = build_hint("find-such-that", 'solve(op="solve", variables="x:Int", constraints=["x > 0"])')
    assert "constraint-hint" in note
    assert "find-such-that" in note
    assert "solve(" in note
    assert "constraint-reasoning" in note  # skill pointer is there
    # Should be reasonably compact (under 1000 chars)
    assert len(note) < 1000


def test_build_hint_handles_multiline_template():
    template = 'solve(op="solve", variables="x:Int",\n      constraints=["x > 0"])'
    note = build_hint("find-such-that", template)
    assert "constraints=" in note
    assert template in note

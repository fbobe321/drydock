"""Tests for `scripts.hle_eval.score_answer` — the empty-method split.

After 2026-05-14 commit d022572, `method='empty'` is sub-classified:

  - empty:no_response       — msg_count <= 1 (model never started)
  - empty:no_final_answer   — msg_count > 1  (model engaged, no FINAL ANSWER)

These need different remediation (the first is a quant/timeout/harness
problem; the second is a retrieval/prompt-rule problem), so the
classifier downstream and the autonomous_review pipeline must be able
to differentiate them via the result record's `method` field.

Tests are kept tight and offline — `score_answer` doesn't need the
LLM judge to validate the empty-path branches.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def hle_eval_mod():
    """Load scripts/hle_eval.py as a module."""
    src = Path("/data3/drydock/scripts/hle_eval.py")
    spec = importlib.util.spec_from_file_location("hle_eval", src)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hle_eval"] = mod
    spec.loader.exec_module(mod)
    return mod


def _q(answer="42"):
    return {"answer": answer, "answer_type": "exactMatch", "question": "Q?"}


def test_empty_pred_no_response_when_msg_count_is_one(hle_eval_mod):
    """msg_count == 1 → empty:no_response (only user message landed)."""
    outcome = {"msg_count": 1}
    result = hle_eval_mod.score_answer(_q(), "", outcome)
    assert result["method"] == "empty:no_response"
    assert result["verdict"] == "NO"
    assert result["correct"] is False


def test_empty_pred_no_response_when_msg_count_is_zero(hle_eval_mod):
    """msg_count == 0 (truly no session) → empty:no_response."""
    outcome = {"msg_count": 0}
    result = hle_eval_mod.score_answer(_q(), "", outcome)
    assert result["method"] == "empty:no_response"


def test_empty_pred_no_final_answer_when_msg_count_is_multi(hle_eval_mod):
    """msg_count > 1 → empty:no_final_answer (engaged but no extraction)."""
    outcome = {"msg_count": 7}
    result = hle_eval_mod.score_answer(_q(), "", outcome)
    assert result["method"] == "empty:no_final_answer"
    assert result["verdict"] == "NO"


def test_empty_pred_falls_back_to_plain_empty_when_outcome_missing(hle_eval_mod):
    """Test/manual-grading callers that don't pass `outcome` must get
    the original `method='empty'` (backwards-compatible)."""
    result = hle_eval_mod.score_answer(_q(), "")
    assert result["method"] == "empty"


def test_exact_match_short_circuits_judge(hle_eval_mod):
    """Exact match doesn't call the judge — verdict YES without
    network."""
    result = hle_eval_mod.score_answer(_q(answer="42"), "42")
    assert result["method"] == "exact"
    assert result["verdict"] == "YES"
    assert result["correct"] is True


def test_empty_pred_with_whitespace_still_treats_as_empty(hle_eval_mod):
    """A pred of '   \n  ' is empty for scoring purposes."""
    outcome = {"msg_count": 9}
    result = hle_eval_mod.score_answer(_q(), "   \n  ", outcome)
    assert result["method"] == "empty:no_final_answer"


# ============================================================================
# _extract_answer — robustness against real-world formats
# ============================================================================

def _assistant(content):
    return {"role": "assistant", "content": content}


def test_extract_answer_plain_final_answer(hle_eval_mod):
    msgs = [_assistant("Working on it...\nFINAL ANSWER: 42")]
    assert hle_eval_mod._extract_answer(msgs) == "42"


def test_extract_answer_handles_markdown_bold(hle_eval_mod):
    """Model often emits `**FINAL ANSWER:** x`. The marker regex must
    swallow the bold and return just `x`."""
    msgs = [_assistant("Steps...\n**FINAL ANSWER:** 7")]
    assert hle_eval_mod._extract_answer(msgs) == "7"


def test_extract_answer_unwraps_boxed_math(hle_eval_mod):
    r"""`FINAL ANSWER: \boxed{e^{\gamma}}` → `e^{\gamma}`. \boxed{} is the
    standard math-paper way to mark a final answer; the judge would
    otherwise compare the wrapped form to the gold."""
    msgs = [_assistant(r"FINAL ANSWER: \boxed{e^{\gamma}}")]
    assert hle_eval_mod._extract_answer(msgs) == r"e^{\gamma}"


def test_extract_answer_strips_math_dollars(hle_eval_mod):
    """`FINAL ANSWER: $\frac{1}{2}$` → `\frac{1}{2}`."""
    msgs = [_assistant(r"FINAL ANSWER: $\frac{1}{2}$")]
    assert hle_eval_mod._extract_answer(msgs) == r"\frac{1}{2}"


def test_extract_answer_handles_multipart_content(hle_eval_mod):
    """Anthropic-style multipart content (list of dicts) flattens."""
    msgs = [_assistant([
        {"type": "text", "text": "thinking out loud"},
        {"type": "text", "text": "FINAL ANSWER: yes"},
    ])]
    assert hle_eval_mod._extract_answer(msgs) == "yes"


def test_extract_answer_falls_back_to_tail_no_marker(hle_eval_mod):
    """When no FINAL ANSWER: marker, returns last 500-char tail."""
    text = "Some explanation here.\nThe answer should be xyz."
    msgs = [_assistant(text)]
    out = hle_eval_mod._extract_answer(msgs)
    assert "xyz" in out


def test_extract_answer_ignores_empty_assistant_turns(hle_eval_mod):
    """If the last assistant has no content, scan back to one that does."""
    msgs = [
        _assistant("FINAL ANSWER: 42"),
        _assistant(""),
        _assistant(None),
    ]
    assert hle_eval_mod._extract_answer(msgs) == "42"


def test_extract_answer_lowercase_marker(hle_eval_mod):
    """`Final Answer: x` (mixed case) must match too."""
    msgs = [_assistant("Final Answer: blue")]
    assert hle_eval_mod._extract_answer(msgs) == "blue"

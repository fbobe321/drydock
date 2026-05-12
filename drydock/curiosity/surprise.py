"""Surprise scorer — quantify the gap between a model assertion and
the evidence it should have respected.

The PRD §5.7 directive "treat surprise as signal, not noise" needs a
concrete trigger. This module provides the trigger: a small set of
detectors that compare the model's output against retrieved evidence,
tool results, or judge verdicts and return a 0.0–1.0 surprise score.

Phase 1 (now): the simple-and-loud detectors that handle the cases the
PRD calls out — HLE judge verdicts disagreeing with the model's answer,
retrieve content overlap with model output, tool results contradicting
recent assertions.

Phase 3+: probability-calibration variants once we can read token-level
logprobs from vLLM.

Public surface:

    score = score_surprise(assertion, evidence, kind="retrieve")  # 0.0–1.0

    if score >= SURPRISE_THRESHOLD:
        enqueue(CuriosityItem(kind=CuriosityKind.EVIDENCE_CONFLICT, ...))
"""
from __future__ import annotations

import re
from typing import Literal

EvidenceKind = Literal["retrieve", "tool_result", "judge_verdict"]

SURPRISE_THRESHOLD = 0.6


def _normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    return " ".join(s.split())


def _token_set(s: str, *, min_len: int = 3) -> set[str]:
    return {t for t in _normalize(s).split() if len(t) >= min_len}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def score_surprise(
    assertion: str,
    evidence: str,
    kind: EvidenceKind = "retrieve",
) -> float:
    """Return a surprise score in [0.0, 1.0].

    Higher = the model said something the evidence does not support.

    Heuristics by `kind`:
    - "judge_verdict": evidence is the HLE judge's verbatim verdict. If
      it contains negation markers ("no answer extracted", "incorrect",
      "wrong", "did not", "fails to"), surprise is high (the model
      believed it had answered; the judge says otherwise).
    - "retrieve": low token-overlap between the model's assertion and
      the retrieved evidence means the model wasn't using the evidence.
      That's the project_graphrag_underused.md failure mode — content
      retrieved, content ignored.
    - "tool_result": presence of error markers in the result
      ("<tool_error>", "Traceback", "AssertionError") combined with a
      confident-sounding assertion ("works", "passes", "correct")
      flags a confident-but-wrong claim.
    """
    if not assertion or not evidence:
        return 0.0

    if kind == "judge_verdict":
        ev_lc = evidence.lower()
        markers = (
            "no answer extracted",
            "incorrect",
            "wrong",
            "did not",
            "fails to",
            "not equivalent",
            "does not match",
        )
        if any(m in ev_lc for m in markers):
            return 0.9
        return 0.1

    if kind == "retrieve":
        a = _token_set(assertion)
        e = _token_set(evidence)
        overlap = _jaccard(a, e)
        # Low overlap → model is answering from prior, not from
        # retrieved evidence. Invert + clamp.
        return max(0.0, min(1.0, 1.0 - overlap * 1.5))

    if kind == "tool_result":
        err_markers = ("<tool_error>", "traceback", "assertionerror", "exception:")
        confidence_markers = ("works", "passes", "succeeds", "correct", "verified")
        ev_lc = evidence.lower()
        as_lc = assertion.lower()
        has_err = any(m in ev_lc for m in err_markers)
        has_confidence = any(m in as_lc for m in confidence_markers)
        if has_err and has_confidence:
            return 0.85
        if has_err:
            return 0.4
        return 0.0

    return 0.0

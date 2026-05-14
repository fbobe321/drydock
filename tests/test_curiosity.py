"""Tests for the Curiosity Layer (SOVEREIGN_PRD §5.7).

Covers:
- gap_detector heuristics (Title Case phrases, acronyms, identifiers,
  quoted entities, version-like tokens)
- surprise.score_surprise across the three evidence kinds
- queue: enqueue + dedup + read_recent roundtrip
- CuriosityItem.fingerprint stability
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drydock.curiosity import (
    CuriosityItem,
    CuriosityKind,
    detect_gaps,
    enqueue,
    queue_path,
    read_recent,
    score_surprise,
)


# ============================================================================
# Gap detector
# ============================================================================

class TestGapDetector:
    def test_empty_returns_empty(self):
        assert detect_gaps("") == []
        assert detect_gaps("   ") == []

    def test_picks_up_quoted_entities(self):
        gaps = detect_gaps('Read the paper "Attention Is All You Need" and summarize.')
        assert "Attention Is All You Need" in gaps

    def test_picks_up_title_case_phrase(self):
        gaps = detect_gaps("Tell me about Retrieval Augmented Generation.")
        # Either the full phrase or "Retrieval Augmented Generation"
        # depending on regex greediness — at minimum some part of it.
        joined = "|".join(gaps)
        assert "Retrieval" in joined and "Generation" in joined

    def test_picks_up_acronyms_minimum_three(self):
        gaps = detect_gaps("Configure MCP and check the FAISS index.")
        assert "MCP" in gaps
        assert "FAISS" in gaps

    def test_rejects_two_letter_acronyms(self):
        # "OK", "AI" are too short / too noisy.
        gaps = detect_gaps("Is the OS OK for AI use?")
        assert "OK" not in gaps
        assert "OS" not in gaps
        assert "AI" not in gaps

    def test_picks_up_dotted_identifier(self):
        gaps = detect_gaps("Use sklearn.metrics.f1_score on the output.")
        assert any("sklearn.metrics" in g for g in gaps)

    def test_picks_up_snake_case_identifier(self):
        gaps = detect_gaps("Where is queue_user_injection defined?")
        assert "queue_user_injection" in gaps

    def test_picks_up_versioned_package(self):
        gaps = detect_gaps("Pin django-4.2.1 in the requirements.")
        assert "django-4.2.1" in gaps

    def test_picks_up_path(self):
        gaps = detect_gaps("Check /data3/drydock/scripts/hle_eval.py for the bug.")
        assert any("hle_eval.py" in g for g in gaps)

    def test_drops_imperative_sentence_starter(self):
        # "Fix the auth bug" — "Fix" is a sentence-opener stopword;
        # "auth" alone isn't Title Case. Either way "Fix" must not
        # show up as a candidate gap.
        gaps = detect_gaps("Fix the auth bug.")
        assert "Fix" not in gaps

    def test_dedupe_preserves_order(self):
        gaps = detect_gaps("MCP server config: MCP needs MCP support.")
        assert gaps.count("MCP") == 1

    def test_max_gaps_cap(self):
        # 12 distinct acronyms but cap is 8.
        text = " ".join(f"ABC{i}" for i in range(12))
        gaps = detect_gaps(text)
        assert len(gaps) <= 8

    def test_drops_hle_template_tokens(self):
        """HLE-style boilerplate (FINAL, ANSWER, QUESTION) must not be
        enqueued as 'unknown terms'. The 2026-05-14 queue audit found
        these accounted for 180+ false positives."""
        text = (
            "Answer this question. End your response with the literal "
            "string FINAL ANSWER: followed by your answer.\n"
            "QUESTION: Consider the elliptic curve E."
        )
        gaps = detect_gaps(text)
        for noise in ("FINAL", "ANSWER", "QUESTION", "FINAL ANSWER"):
            assert noise not in gaps, f"detected noise: {noise!r} in {gaps}"

    def test_drops_consider_suppose_given_prose_openers(self):
        """Mathematical prose openers (Consider, Suppose, Given, Let)
        consume Title-Case + glue words downstream. Drop them at the
        leading stopword so the Title-Case phrase regex doesn't emit
        'Consider the lattice'-style noise."""
        gaps = detect_gaps("Consider the lattice. Suppose X is closed.")
        for noise in ("Consider", "Suppose", "Consider the"):
            assert noise not in gaps, f"detected noise: {noise!r} in {gaps}"

    def test_keeps_real_terms_amid_hle_noise(self):
        """Validates the fix isn't over-broad: real entity names must
        survive alongside the boilerplate."""
        text = (
            "QUESTION: Consider the elliptic curve and the Dirichlet "
            "character chi_1 mod p. FINAL ANSWER:"
        )
        gaps = detect_gaps(text)
        # chi_1 is a snake_case identifier — must persist.
        assert any("chi_1" in g for g in gaps), gaps


# ============================================================================
# Surprise scorer
# ============================================================================

class TestSurpriseScorer:
    def test_empty_inputs_return_zero(self):
        assert score_surprise("", "evidence", "retrieve") == 0.0
        assert score_surprise("assertion", "", "retrieve") == 0.0

    def test_judge_verdict_negation_high_score(self):
        s = score_surprise(
            "The answer is 42.",
            "Judge: no answer extracted",
            kind="judge_verdict",
        )
        assert s >= 0.8

    def test_judge_verdict_neutral_low_score(self):
        s = score_surprise(
            "The answer is 42.",
            "Judge: equivalent to gold",
            kind="judge_verdict",
        )
        assert s < 0.3

    def test_retrieve_overlap_high_low_surprise(self):
        # Assertion mostly mentions same tokens as evidence → low surprise.
        s = score_surprise(
            "GraphRAG combines retrieval and graph reasoning",
            "GraphRAG combines retrieval and graph reasoning for QA",
            kind="retrieve",
        )
        assert s < 0.6

    def test_retrieve_no_overlap_high_surprise(self):
        # Assertion ignores evidence entirely → high surprise.
        s = score_surprise(
            "The capital of France is Berlin.",
            "Paris has been the capital of France since the Middle Ages.",
            kind="retrieve",
        )
        assert s >= 0.5

    def test_tool_result_error_with_confident_claim_flags(self):
        s = score_surprise(
            "All tests pass and the code works correctly.",
            "<tool_error>\nTraceback (most recent call last):\nAssertionError: ...",
            kind="tool_result",
        )
        assert s >= 0.7

    def test_tool_result_error_without_confidence_lower(self):
        s = score_surprise(
            "Running the test now.",
            "<tool_error>\nTraceback: failure",
            kind="tool_result",
        )
        # Some surprise (error happened) but not the confident-but-wrong
        # signature.
        assert 0.0 < s < 0.7


# ============================================================================
# Queue (JSONL roundtrip + dedup)
# ============================================================================

class TestCuriosityQueue:
    def test_enqueue_writes_jsonl(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv(
            "DRYDOCK_CURIOSITY_QUEUE", str(tmp_path / "curiosity.jsonl")
        )
        item = CuriosityItem(
            kind=CuriosityKind.UNKNOWN_TERM,
            term="GraphRAG",
            context="user mentioned GraphRAG in their question",
            source="user_input",
        )
        wrote = enqueue(item)
        assert wrote is True
        # File exists with one line.
        path = queue_path()
        assert path.is_file()
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        d = json.loads(lines[0])
        assert d["kind"] == "unknown_term"
        assert d["term"] == "GraphRAG"
        # enqueue populated id and ts.
        assert d["id"]
        assert d["ts"]

    def test_enqueue_dedupes_identical(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv(
            "DRYDOCK_CURIOSITY_QUEUE", str(tmp_path / "curiosity.jsonl")
        )
        item = CuriosityItem(
            kind=CuriosityKind.UNKNOWN_TERM,
            term="GraphRAG",
            context="...",
            source="user_input",
        )
        assert enqueue(item) is True
        # Identical fingerprint within window → dedup.
        item2 = CuriosityItem(
            kind=CuriosityKind.UNKNOWN_TERM,
            term="GraphRAG",
            context="different context, same fingerprint",
            source="user_input",
        )
        assert enqueue(item2) is False
        # Only one line on disk.
        assert len(queue_path().read_text().strip().splitlines()) == 1

    def test_enqueue_different_kinds_not_deduped(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "DRYDOCK_CURIOSITY_QUEUE", str(tmp_path / "curiosity.jsonl")
        )
        assert enqueue(CuriosityItem(
            kind=CuriosityKind.UNKNOWN_TERM,
            term="GraphRAG", context="x", source="s",
        )) is True
        assert enqueue(CuriosityItem(
            kind=CuriosityKind.HLE_FAILURE,
            term="GraphRAG", context="x", source="s",
        )) is True
        assert len(queue_path().read_text().strip().splitlines()) == 2

    def test_read_recent_returns_dicts(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "DRYDOCK_CURIOSITY_QUEUE", str(tmp_path / "curiosity.jsonl")
        )
        for i in range(3):
            enqueue(CuriosityItem(
                kind=CuriosityKind.UNKNOWN_TERM,
                term=f"Term{i}", context="x", source=f"s{i}",
            ))
        recent = read_recent(limit=10)
        assert len(recent) == 3
        assert all("term" in r and "kind" in r for r in recent)

    def test_read_recent_missing_file_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "DRYDOCK_CURIOSITY_QUEUE", str(tmp_path / "nope.jsonl")
        )
        assert read_recent() == []


# ============================================================================
# Fingerprint stability
# ============================================================================

class TestFingerprint:
    def test_same_term_kind_source_same_fingerprint(self):
        a = CuriosityItem(
            kind=CuriosityKind.UNKNOWN_TERM, term="X",
            context="...", source="user",
        )
        b = CuriosityItem(
            kind=CuriosityKind.UNKNOWN_TERM, term="X",
            context="totally different context", source="user",
        )
        assert a.fingerprint() == b.fingerprint()

    def test_different_source_different_fingerprint(self):
        a = CuriosityItem(
            kind=CuriosityKind.UNKNOWN_TERM, term="X",
            context="x", source="user",
        )
        b = CuriosityItem(
            kind=CuriosityKind.UNKNOWN_TERM, term="X",
            context="x", source="hle:42",
        )
        assert a.fingerprint() != b.fingerprint()

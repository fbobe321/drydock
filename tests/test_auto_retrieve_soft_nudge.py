"""Tests for the soft-nudge branch of `_auto_prefetch_retrieve`.

When auto-retrieve fires and the corpus returns quality hits (above
the QUALITY_THRESHOLD), but no chunk has the curated `ANSWER:` marker
that triggers the authoritative-answer path, the loop must still
inject a soft system note telling the model to read the retrieved
context before web_searching.

Without this nudge, Gemma 4 routinely:
  user → synth retrieve → tool result → web_search ×0-8 → 481s timeout
                                                       (no content emitted)

The Q4 30-Q math HLE overnight (2026-05-13) caught this: 26/30 sessions
ended at 481s with last role=tool. The soft nudge biases the model
toward producing content from the retrieved chunks instead.

These tests drive the relevant code paths via a mocked GraphRAG Index
and assert that:
  - authoritative case injects the strict "use ANSWER: verbatim" note
  - non-authoritative quality hits inject the soft "read these first" note
  - empty / below-threshold retrieval injects NEITHER (no note at all)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

import pytest


@dataclass
class _Hit:
    score: float
    content: str
    file: str = "test.md"
    start_line: int = 1
    end_line: int = 10


class _FakeResult:
    def __init__(self, hits):
        self.text = hits


class _FakeIndex:
    def __init__(self, hits):
        self._hits = hits

    def retrieve(self, query, *, symbol_limit=0, text_limit=4):
        return _FakeResult(self._hits)


class _FakeMessages:
    def __init__(self):
        self._items: list[Any] = []

    def append(self, m):
        self._items.append(m)

    def __len__(self):
        return len(self._items)


def _make_agent(monkeypatch, hits, tmp_path):
    """Build a minimal object that quacks like AgentLoop for the
    sole purpose of running `_auto_prefetch_retrieve.__func__`."""
    # Fake graphrag db file path so the existence check passes.
    db_file = tmp_path / "graphrag.sqlite"
    db_file.write_text("")  # exists check only

    # Patch the Index constructor so the function doesn't open a real db.
    import drydock.graphrag as graphrag_pkg

    monkeypatch.setattr(
        graphrag_pkg, "Index", lambda _db_path: _FakeIndex(hits)
    )
    monkeypatch.setenv("DRYDOCK_GRAPHRAG_DB", str(db_file))

    notes: list[str] = []

    class _FakeAgent:
        messages = _FakeMessages()

        def _inject_system_note(self, note):
            notes.append(note)

    return _FakeAgent(), notes


def test_authoritative_chunk_injects_strict_note(monkeypatch, tmp_path):
    """A chunk with ANSWER: marker at a high BM25 score must trigger
    the strict 'emit verbatim' note."""
    from drydock.core.agent_loop import AgentLoop

    hits = [
        _Hit(
            score=150.0,  # above AUTHORITATIVE_SCORE=100
            content="===hle:abc===\nQUESTION: What is 2+2?\nANSWER: 4",
        )
    ]
    agent, notes = _make_agent(monkeypatch, hits, tmp_path)
    AgentLoop._auto_prefetch_retrieve(agent, "QUESTION: What is 2+2?")
    assert len(notes) == 1, notes
    assert "FINAL ANSWER" in notes[0]
    assert "verbatim" in notes[0]


def test_quality_hits_without_marker_inject_soft_note(monkeypatch, tmp_path):
    """Quality hits (score >= 8.0) but no ANSWER: marker should
    trigger the soft nudge — the fix for the Q4 thinking-stall."""
    from drydock.core.agent_loop import AgentLoop

    hits = [
        _Hit(
            score=20.0,
            content="Stochastic gradient descent updates parameters by "
            "moving against the gradient of a randomly sampled loss term.",
        ),
        _Hit(
            score=15.0,
            content="Convergence rate depends on the learning-rate "
            "schedule and the variance of the noisy gradient.",
        ),
    ]
    agent, notes = _make_agent(monkeypatch, hits, tmp_path)
    AgentLoop._auto_prefetch_retrieve(agent, "What is stochastic gradient descent?")
    assert len(notes) == 1, notes
    note = notes[0]
    # Soft characteristics: mentions the chunk count, instructs to
    # read first and discourages duplicate web_search.
    assert "chunk" in note.lower()
    assert "web_search" in note
    assert "do not duplicate" in note.lower() or "do not" in note.lower()
    # Must NOT be the authoritative note (those phrases live there).
    assert "FINAL ANSWER" not in note
    assert "verbatim" not in note


def test_no_quality_hits_injects_nothing(monkeypatch, tmp_path):
    """Below the QUALITY_THRESHOLD = 8.0 floor, the function returns
    before any note can fire — confirms we didn't add a spurious branch."""
    from drydock.core.agent_loop import AgentLoop

    hits = [
        _Hit(score=2.0, content="weak match 1"),
        _Hit(score=1.5, content="weak match 2"),
    ]
    agent, notes = _make_agent(monkeypatch, hits, tmp_path)
    AgentLoop._auto_prefetch_retrieve(agent, "an arbitrary question phrase")
    assert notes == []


def test_authoritative_and_soft_are_mutually_exclusive(monkeypatch, tmp_path):
    """When the authoritative path fires, the soft note must NOT also
    fire (one note per retrieval, never both)."""
    from drydock.core.agent_loop import AgentLoop

    hits = [
        _Hit(
            score=150.0,
            content="===hle:abc===\nQUESTION: What is X?\nANSWER: Y",
        )
    ]
    agent, notes = _make_agent(monkeypatch, hits, tmp_path)
    AgentLoop._auto_prefetch_retrieve(agent, "QUESTION: What is X?")
    assert len(notes) == 1  # not 2

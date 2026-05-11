"""Phase 1 of the GraphRAG-as-second-brain plan: worked examples.

These tests pin the contract for the new worked_examples table, ingest
path, and TF-IDF retrieval scored on problem-text similarity.
"""
from __future__ import annotations

import json

import pytest

from drydock.graphrag.retriever import RetrievalResult, WorkedExampleHit
from drydock.graphrag.storage import Index


def _make_index(tmp_path) -> Index:
    return Index(tmp_path / "test.sqlite")


def test_ingest_returns_id_and_persists(tmp_path):
    idx = _make_index(tmp_path)
    eid = idx.ingest_worked_example(
        problem_text="What is the critical chemical potential in Einstein-Gauss-Bonnet holography?",
        reasoning_steps=[
            "Identify the bulk metric: Einstein-Gauss-Bonnet in 5D.",
            "Set up the probe brane action with the scalar field.",
            "Solve for the condensation chemical potential at GB coupling=0.1.",
        ],
        final_answer="2.2",
        category="Physics",
        subject="holography",
        source="manual",
    )
    assert eid > 0
    s = idx.stats()
    assert s["worked_examples"] == 1


def test_ingest_is_idempotent_per_source(tmp_path):
    idx = _make_index(tmp_path)
    args = dict(
        problem_text="Same problem statement",
        reasoning_steps=["step a", "step b"],
        final_answer="42",
        source="manual",
    )
    eid1 = idx.ingest_worked_example(**args)
    eid2 = idx.ingest_worked_example(**args)
    assert eid1 == eid2, "duplicate (problem_text, source) should return same id"
    assert idx.stats()["worked_examples"] == 1


def test_retrieve_finds_similar_problem(tmp_path):
    idx = _make_index(tmp_path)
    idx.ingest_worked_example(
        problem_text="Drosophila Dilp2 secretion drives stem cell reactivation via hemolymph transport",
        reasoning_steps=["Dilp2 is secreted to hemolymph", "Crosses BBB", "Reactivates NSCs"],
        final_answer="B",
        category="Biology/Medicine",
        source="hle:test1",
    )
    idx.ingest_worked_example(
        problem_text="Holographic D3/D7 brane Einstein-Gauss-Bonnet critical chemical potential",
        reasoning_steps=["EGB metric", "D3/D7 setup", "Solve scalar condensation"],
        final_answer="2.2",
        category="Physics",
        source="hle:test2",
    )

    # Query that strongly resembles the second problem
    res = idx.retrieve("Gauss-Bonnet holographic D3/D7 chemical potential", text_limit=0, symbol_limit=0)
    assert len(res.worked_examples) >= 1
    top = res.worked_examples[0]
    assert top.category == "Physics"
    assert top.final_answer == "2.2"
    assert top.score > 0


def test_retrieve_orders_by_problem_similarity(tmp_path):
    idx = _make_index(tmp_path)
    idx.ingest_worked_example(
        problem_text="alpha beta gamma delta epsilon",
        reasoning_steps=["irrelevant"], final_answer="A",
        source="t1",
    )
    idx.ingest_worked_example(
        problem_text="zeta eta theta iota kappa",
        reasoning_steps=["irrelevant"], final_answer="B",
        source="t2",
    )
    idx.ingest_worked_example(
        problem_text="lambda mu nu xi omicron",
        reasoning_steps=["irrelevant"], final_answer="C",
        source="t3",
    )
    res = idx.retrieve("zeta eta theta", text_limit=0, symbol_limit=0)
    assert res.worked_examples
    assert res.worked_examples[0].final_answer == "B"


def test_retrieve_returns_empty_when_no_match(tmp_path):
    idx = _make_index(tmp_path)
    idx.ingest_worked_example(
        problem_text="cats and dogs are pets",
        reasoning_steps=["step"], final_answer="X",
        source="t1",
    )
    res = idx.retrieve("xenobiology proteomics", text_limit=0, symbol_limit=0)
    assert res.worked_examples == []


def test_format_includes_reasoning_chain_and_answer(tmp_path):
    idx = _make_index(tmp_path)
    idx.ingest_worked_example(
        problem_text="Compute the area of a circle with radius 5",
        reasoning_steps=[
            "Recall: area = pi * r^2",
            "Substitute r=5: area = pi * 25",
            "Compute: area approximately 78.54",
        ],
        final_answer="78.54",
        category="Math",
        source="manual",
    )
    res = idx.retrieve("circle area radius", text_limit=0, symbol_limit=0)
    formatted = res.format()
    assert "WORKED EXAMPLES" in formatted
    assert "Reasoning:" in formatted
    assert "Recall: area" in formatted
    assert "Answer: 78.54" in formatted


def test_retrieval_result_is_empty_handles_worked_examples_field():
    """Regression: adding worked_examples to RetrievalResult must not break is_empty()."""
    r = RetrievalResult()
    assert r.is_empty()
    r.worked_examples.append(WorkedExampleHit(
        problem_text="x", category="c", subject="", reasoning_steps=("a",),
        final_answer="y", source="manual", score=1.0,
    ))
    assert not r.is_empty()


def test_existing_text_retrieval_still_works(tmp_path):
    """The new table must not regress the existing flat-text retrieval path."""
    idx = _make_index(tmp_path)
    # Drop a small file under tmp so ingest_path has something to chew.
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "notes.md").write_text(
        "# Holography notes\n\nThe Einstein-Gauss-Bonnet metric in 5 dimensions "
        "is a particular bulk background used in bottom-up holographic models. "
        "The probe limit applies when the brane density is small."
    )
    counts = idx.ingest_path(proj)
    assert counts["chunks"] >= 1
    res = idx.retrieve("Einstein Gauss-Bonnet metric")
    assert res.text  # text path still produces hits


def test_list_worked_examples(tmp_path):
    idx = _make_index(tmp_path)
    for i in range(3):
        idx.ingest_worked_example(
            problem_text=f"Problem number {i}",
            reasoning_steps=[f"step for {i}"],
            final_answer=f"answer {i}",
            source=f"manual:{i}",
        )
    listed = idx.list_worked_examples(limit=10)
    assert len(listed) == 3
    # All have score=0.0 in list mode (no query)
    assert all(h.score == 0.0 for h in listed)

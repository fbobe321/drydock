"""Tests for scripts/hle_aggregate.py — multi-batch HLE rollup."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def agg_mod():
    src = Path("/data3/drydock/scripts/hle_aggregate.py")
    spec = importlib.util.spec_from_file_location("hle_aggregate", src)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hle_aggregate"] = mod
    spec.loader.exec_module(mod)
    return mod


def _mkrun(root: Path, name: str, results: list[dict]) -> Path:
    """Create a run_<ts>/results.jsonl tree mirroring the live shape."""
    run = root / name
    run.mkdir()
    (run / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in results) + "\n"
    )
    return run


def test_aggregate_empty_root(agg_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(agg_mod, "RESULTS_ROOT", tmp_path)
    rep = agg_mod.aggregate(agg_mod._iter_runs(None))
    assert rep["total"] == 0
    assert rep["correct"] == 0
    assert rep["score"] == 0.0


def test_aggregate_counts_correct_and_total(agg_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(agg_mod, "RESULTS_ROOT", tmp_path)
    _mkrun(tmp_path, "run_1", [
        {"category": "Math", "correct": True, "method": "exact"},
        {"category": "Math", "correct": False, "method": "empty"},
        {"category": "Physics", "correct": True, "method": "judge"},
    ])
    rep = agg_mod.aggregate(agg_mod._iter_runs(None))
    assert rep["total"] == 3
    assert rep["correct"] == 2
    assert rep["score"] == 2 / 3
    assert rep["by_category"]["Math"]["total"] == 2
    assert rep["by_category"]["Math"]["correct"] == 1
    assert rep["by_category"]["Physics"]["correct"] == 1


def test_aggregate_groups_methods(agg_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(agg_mod, "RESULTS_ROOT", tmp_path)
    _mkrun(tmp_path, "run_1", [
        {"category": "Math", "method": "exact", "correct": True},
        {"category": "Math", "method": "empty:no_response", "correct": False},
        {"category": "Math", "method": "empty:no_final_answer", "correct": False},
    ])
    rep = agg_mod.aggregate(agg_mod._iter_runs(None))
    assert rep["by_method"]["exact"] == 1
    assert rep["by_method"]["empty:no_response"] == 1
    assert rep["by_method"]["empty:no_final_answer"] == 1


def test_aggregate_per_batch_breakdown(agg_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(agg_mod, "RESULTS_ROOT", tmp_path)
    r1 = _mkrun(tmp_path, "run_a", [
        {"category": "Math", "correct": True},
        {"category": "Math", "correct": False},
    ])
    # Force the second run to be newer so per_batch ordering is testable.
    time.sleep(0.01)
    r2 = _mkrun(tmp_path, "run_b", [
        {"category": "Physics", "correct": False},
    ])
    rep = agg_mod.aggregate(agg_mod._iter_runs(None))
    assert len(rep["per_batch"]) == 2
    # Sorted by mtime
    assert rep["per_batch"][0]["run"] == "run_a"
    assert rep["per_batch"][1]["run"] == "run_b"
    assert rep["per_batch"][0]["score"] == 0.5


def test_aggregate_handles_missing_results_jsonl(agg_mod, tmp_path, monkeypatch):
    """An empty run_*/ directory must not crash the aggregator."""
    monkeypatch.setattr(agg_mod, "RESULTS_ROOT", tmp_path)
    (tmp_path / "run_empty").mkdir()  # no results.jsonl
    rep = agg_mod.aggregate(agg_mod._iter_runs(None))
    assert rep["total"] == 0


def test_aggregate_handles_malformed_jsonl(agg_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(agg_mod, "RESULTS_ROOT", tmp_path)
    run = tmp_path / "run_bad"
    run.mkdir()
    (run / "results.jsonl").write_text(
        '{"category":"Math","correct":true}\n'
        'this is not json\n'
        '\n'
        '{"category":"Math","correct":false}\n'
    )
    rep = agg_mod.aggregate(agg_mod._iter_runs(None))
    assert rep["total"] == 2
    assert rep["correct"] == 1


def test_format_text_includes_score_and_categories(agg_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(agg_mod, "RESULTS_ROOT", tmp_path)
    _mkrun(tmp_path, "run_x", [
        {"category": "Math", "correct": True},
        {"category": "Math", "correct": False},
    ])
    rep = agg_mod.aggregate(agg_mod._iter_runs(None))
    out = agg_mod._format_text(rep)
    assert "score: 50.0%" in out
    assert "Math" in out
    assert "run_x" in out

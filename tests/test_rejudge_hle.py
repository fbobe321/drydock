"""Tests for scripts/rejudge_hle.py — re-evaluate ERROR verdicts.

The judge-fix in bc12eee changed verdicts retroactively; this
script's job is to walk historical results and apply the fixed
judge to the ERROR rows. Tests below mock the actual `judge_with_gemma`
call so we exercise the file-walking + verdict-rewriting logic
without depending on a live model.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def rj_mod():
    src = Path("/data3/drydock/scripts/rejudge_hle.py")
    spec = importlib.util.spec_from_file_location("rejudge_hle", src)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rejudge_hle"] = mod
    spec.loader.exec_module(mod)
    return mod


def _mkrun(root: Path, name: str, results: list[dict]) -> Path:
    run = root / name
    run.mkdir()
    (run / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in results) + "\n"
    )
    return run


def test_iter_runs_finds_runs(rj_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(rj_mod, "RESULTS_ROOT", tmp_path)
    _mkrun(tmp_path, "run_a", [])
    _mkrun(tmp_path, "run_b", [])
    runs = rj_mod._iter_runs(None)
    assert {r.name for r in runs} == {"run_a", "run_b"}


def test_iter_runs_skips_non_run_dirs(rj_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(rj_mod, "RESULTS_ROOT", tmp_path)
    _mkrun(tmp_path, "run_x", [])
    (tmp_path / "not_a_run").mkdir()
    (tmp_path / "regular_file.txt").write_text("")
    runs = rj_mod._iter_runs(None)
    assert {r.name for r in runs} == {"run_x"}


def test_iter_runs_filters_by_since(rj_mod, tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(rj_mod, "RESULTS_ROOT", tmp_path)
    old = _mkrun(tmp_path, "run_old", [])
    time.sleep(0.05)
    new = _mkrun(tmp_path, "run_new", [])
    # Force old to be 1 day in the past.
    old_ts = (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()
    import os
    os.utime(old, (old_ts, old_ts))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
    runs = rj_mod._iter_runs(cutoff)
    assert {r.name for r in runs} == {"run_new"}


def test_main_dry_run_doesnt_write(rj_mod, tmp_path, monkeypatch, capsys):
    """Dry-run mode (no --apply) should not create rejudged.jsonl or
    rewrite summary.json."""
    monkeypatch.setattr(rj_mod, "RESULTS_ROOT", tmp_path)
    run = _mkrun(tmp_path, "run_x", [
        {
            "id": "q1", "category": "Math", "ground_truth": "42",
            "predicted": "42", "verdict": "ERROR", "method": "judge",
            "question": "what?",
        },
    ])

    # Patch the judge to return YES so we know mock works.
    def fake_judge(q, g, p):
        return ("YES", "matched")

    he_mod = type(sys)("hle_eval")
    he_mod.judge_with_gemma = fake_judge
    monkeypatch.setattr(rj_mod, "_load_hle_eval", lambda: he_mod)

    rc = rj_mod.main(argv=[])  # no --apply
    assert rc == 0
    assert not (run / "rejudged.jsonl").exists()


def test_main_apply_writes_rejudged(rj_mod, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rj_mod, "RESULTS_ROOT", tmp_path)
    run = _mkrun(tmp_path, "run_x", [
        {
            "id": "q1", "category": "Math", "ground_truth": "42",
            "predicted": "42", "verdict": "ERROR", "method": "judge",
            "question": "what?",
        },
        {
            "id": "q2", "category": "Math", "ground_truth": "12",
            "predicted": "13", "verdict": "ERROR", "method": "judge",
            "question": "what?",
        },
    ])

    def fake_judge(q, g, p):
        if p == "42":
            return ("YES", "matched")
        return ("NO", "no match")

    he_mod = type(sys)("hle_eval")
    he_mod.judge_with_gemma = fake_judge
    monkeypatch.setattr(rj_mod, "_load_hle_eval", lambda: he_mod)

    rc = rj_mod.main(argv=["--apply", "--apply-to-summary"])
    assert rc == 0
    rej = run / "rejudged.jsonl"
    assert rej.exists()
    lines = [json.loads(l) for l in rej.read_text().splitlines()]
    assert len(lines) == 2
    by_id = {l["id"]: l for l in lines}
    assert by_id["q1"]["verdict"] == "YES"
    assert by_id["q1"]["correct"] is True
    assert by_id["q1"]["original_verdict"] == "ERROR"
    assert by_id["q2"]["verdict"] == "NO"
    assert by_id["q2"]["correct"] is False
    # Summary should now reflect the rejudge.
    summary = json.loads((run / "summary.json").read_text())
    assert summary["correct"] == 1
    assert summary["total"] == 2


def test_main_skips_rows_with_empty_predicted(rj_mod, tmp_path, monkeypatch):
    """ERROR rows with no predicted text aren't rejudgeable."""
    monkeypatch.setattr(rj_mod, "RESULTS_ROOT", tmp_path)
    _mkrun(tmp_path, "run_x", [
        {
            "id": "q1", "category": "Math", "ground_truth": "42",
            "predicted": "", "verdict": "ERROR", "method": "judge",
            "question": "what?",
        },
    ])

    call_count = [0]
    def fake_judge(q, g, p):
        call_count[0] += 1
        return ("YES", "should not be called")

    he_mod = type(sys)("hle_eval")
    he_mod.judge_with_gemma = fake_judge
    monkeypatch.setattr(rj_mod, "_load_hle_eval", lambda: he_mod)

    rj_mod.main(argv=["--apply"])
    assert call_count[0] == 0  # judge never called for empty pred


def test_main_only_rejudges_error_rows(rj_mod, tmp_path, monkeypatch):
    """Non-ERROR verdicts must NOT be re-judged."""
    monkeypatch.setattr(rj_mod, "RESULTS_ROOT", tmp_path)
    _mkrun(tmp_path, "run_x", [
        {
            "id": "q1", "ground_truth": "42", "predicted": "42",
            "verdict": "YES", "method": "judge", "question": "?",
        },
        {
            "id": "q2", "ground_truth": "1", "predicted": "1",
            "verdict": "exact", "method": "exact", "question": "?",
        },
    ])
    call_count = [0]
    def fake_judge(q, g, p):
        call_count[0] += 1
        return ("YES", "")
    he_mod = type(sys)("hle_eval")
    he_mod.judge_with_gemma = fake_judge
    monkeypatch.setattr(rj_mod, "_load_hle_eval", lambda: he_mod)
    rj_mod.main(argv=["--apply"])
    assert call_count[0] == 0

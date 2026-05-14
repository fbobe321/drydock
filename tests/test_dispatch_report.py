"""Tests for scripts/dispatch_report.py — trailing-window report.

The report script is the observability path for the dispatch queues.
After 2026-05-14 dedup work, the queues now carry honest signal;
this report makes it visible without grepping JSONL files.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def report_mod():
    src = Path("/data3/drydock/scripts/dispatch_report.py")
    spec = importlib.util.spec_from_file_location("dispatch_report", src)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dispatch_report"] = mod
    spec.loader.exec_module(mod)
    return mod


def _now_iso(offset_hours: float = 0.0) -> str:
    t = datetime.now(timezone.utc) + timedelta(hours=offset_hours)
    return t.isoformat()


def _write_queue(root: Path, bucket: str, records: list[dict]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{bucket}.jsonl"
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def test_parse_window_handles_units(report_mod):
    assert report_mod._parse_window("30s") == timedelta(seconds=30)
    assert report_mod._parse_window("5m") == timedelta(minutes=5)
    assert report_mod._parse_window("12h") == timedelta(hours=12)
    assert report_mod._parse_window("7d") == timedelta(days=7)
    assert report_mod._parse_window("2w") == timedelta(weeks=2)


def test_parse_window_rejects_garbage(report_mod):
    with pytest.raises(ValueError):
        report_mod._parse_window("twelve hours")
    with pytest.raises(ValueError):
        report_mod._parse_window("12")
    with pytest.raises(ValueError):
        report_mod._parse_window("h12")


def test_parse_ts_handles_z_and_offset_suffix(report_mod):
    a = report_mod._parse_ts("2026-05-14T00:30:00Z")
    b = report_mod._parse_ts("2026-05-14T00:30:00+00:00")
    assert a is not None and b is not None
    assert a == b


def test_parse_ts_returns_none_on_empty_or_bad(report_mod):
    assert report_mod._parse_ts("") is None
    assert report_mod._parse_ts("not-a-date") is None


def test_report_filters_by_window(report_mod, tmp_path, monkeypatch):
    """A 1h window must include current entries and exclude 2h-old ones."""
    monkeypatch.setattr(report_mod, "QUEUE_ROOT", tmp_path)
    recent = {"pattern_id": "p:fresh", "evidence": "now", "ts": _now_iso()}
    stale = {
        "pattern_id": "p:stale", "evidence": "earlier",
        "ts": _now_iso(offset_hours=-2.5),
    }
    _write_queue(tmp_path, "harness", [recent, stale])
    rep = report_mod.report(timedelta(hours=1))
    assert rep["by_pattern"].get("p:fresh") == 1
    assert "p:stale" not in rep["by_pattern"]


def test_report_handles_missing_ts(report_mod, tmp_path, monkeypatch):
    """Records without ts must NOT be filtered out — keep the count
    honest even when the producer forgot the timestamp."""
    monkeypatch.setattr(report_mod, "QUEUE_ROOT", tmp_path)
    no_ts = {"pattern_id": "p:no_ts", "evidence": "?"}
    _write_queue(tmp_path, "harness", [no_ts])
    rep = report_mod.report(timedelta(hours=1))
    assert rep["by_pattern"].get("p:no_ts") == 1


def test_report_groups_by_bucket(report_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "QUEUE_ROOT", tmp_path)
    _write_queue(tmp_path, "harness", [
        {"pattern_id": "h:a", "evidence": "1", "ts": _now_iso()},
        {"pattern_id": "h:b", "evidence": "2", "ts": _now_iso()},
    ])
    _write_queue(tmp_path, "retrieval", [
        {"pattern_id": "r:a", "evidence": "1", "ts": _now_iso()},
    ])
    rep = report_mod.report(timedelta(hours=1))
    assert rep["by_bucket"] == {"harness": 2, "retrieval": 1}
    assert rep["pattern_to_bucket"]["h:a"] == "harness"
    assert rep["pattern_to_bucket"]["r:a"] == "retrieval"


def test_report_uses_kind_for_curiosity_records(report_mod, tmp_path, monkeypatch):
    """Curiosity items don't have pattern_id; use `kind` as the key."""
    monkeypatch.setattr(report_mod, "QUEUE_ROOT", tmp_path)
    _write_queue(tmp_path, "curiosity", [
        {"kind": "unknown_term", "term": "foo", "ts": _now_iso()},
        {"kind": "hle_failure", "term": "bar", "ts": _now_iso()},
    ])
    rep = report_mod.report(timedelta(hours=1))
    assert rep["by_pattern"].get("unknown_term") == 1
    assert rep["by_pattern"].get("hle_failure") == 1


def test_report_missing_queue_root(report_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "QUEUE_ROOT", tmp_path / "nope")
    rep = report_mod.report(timedelta(hours=1))
    assert "error" in rep


def test_report_skips_malformed_lines(report_mod, tmp_path, monkeypatch):
    """A corrupted line must not abort the whole report."""
    monkeypatch.setattr(report_mod, "QUEUE_ROOT", tmp_path)
    path = tmp_path / "harness.jsonl"
    tmp_path.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"pattern_id": "p:good", "ts": "%s"}\n'
        '{not json at all\n'
        '\n'
        % _now_iso()
    )
    rep = report_mod.report(timedelta(hours=1))
    assert rep["by_pattern"].get("p:good") == 1

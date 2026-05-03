"""Tests for the Dispatcher.

Verifies routing per bucket, dedup, error isolation, and the default
JSONL queue handler behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from drydock.core.classifier import (
    Bucket,
    Classifier,
    Dispatcher,
    FailureSignal,
    classify_text,
    make_jsonl_handler,
)
from drydock.core.classifier.dispatcher import _queue_path_for


def _signal(bucket: Bucket, pid: str, evidence: str = "x") -> FailureSignal:
    return FailureSignal(
        bucket=bucket,
        pattern_id=pid,
        evidence=evidence,
        suggested_action="fix it",
    )


def test_dispatch_routes_per_bucket(tmp_path: Path):
    calls: list[tuple[str, str]] = []
    handlers = {
        b: (lambda s, b=b: calls.append((str(b), s.pattern_id))) for b in Bucket
    }
    d = Dispatcher(handlers=handlers, queue_root=tmp_path)
    signals = [
        _signal(Bucket.HARNESS, "h1", "evA"),
        _signal(Bucket.RETRIEVAL, "r1", "evB"),
        _signal(Bucket.STEERING, "s1", "evC"),
    ]
    result = d.dispatch_all(signals)
    assert result.dispatched == 3
    routed_buckets = sorted(b for b, _ in calls)
    assert "Bucket.HARNESS" in routed_buckets[0] or "harness" in routed_buckets[0]


def test_dispatch_dedupes_repeated_signals(tmp_path: Path):
    calls: list[str] = []
    d = Dispatcher(
        handlers={Bucket.HARNESS: lambda s: calls.append(s.pattern_id)},
        queue_root=tmp_path,
    )
    s = _signal(Bucket.HARNESS, "h1", "same evidence")
    result = d.dispatch_all([s, s, s])
    assert result.dispatched == 1
    assert result.deduped == 2
    assert len(calls) == 1


def test_dispatch_isolates_handler_errors(tmp_path: Path):
    def boom(_signal: FailureSignal) -> None:
        raise RuntimeError("kaboom")
    d = Dispatcher(
        handlers={
            Bucket.HARNESS: boom,
            Bucket.RETRIEVAL: lambda s: None,
        },
        queue_root=tmp_path,
    )
    result = d.dispatch_all([
        _signal(Bucket.HARNESS, "h1", "a"),
        _signal(Bucket.RETRIEVAL, "r1", "b"),
    ])
    # One handler raised; the other still ran.
    assert result.dispatched == 1
    assert any("kaboom" in e for e in result.errors)


def test_default_jsonl_handler_writes_per_bucket(tmp_path: Path):
    d = Dispatcher(queue_root=tmp_path)
    signals = [
        _signal(Bucket.HARNESS, "h1", "harness ev 1"),
        _signal(Bucket.HARNESS, "h2", "harness ev 2"),
        _signal(Bucket.RETRIEVAL, "r1", "retrieval ev"),
    ]
    result = d.dispatch_all(signals)
    assert result.dispatched == 3

    harness_path = _queue_path_for(Bucket.HARNESS, tmp_path)
    retrieval_path = _queue_path_for(Bucket.RETRIEVAL, tmp_path)
    assert harness_path.is_file()
    assert retrieval_path.is_file()

    harness_records = [
        json.loads(line) for line in harness_path.read_text().splitlines()
    ]
    assert len(harness_records) == 2
    assert all("ts" in r for r in harness_records)
    assert {r["pattern_id"] for r in harness_records} == {"h1", "h2"}


def test_summary_contains_per_bucket_counts(tmp_path: Path):
    d = Dispatcher(queue_root=tmp_path)
    signals = [
        _signal(Bucket.HARNESS, "h1", "a"),
        _signal(Bucket.HARNESS, "h2", "b"),
        _signal(Bucket.STEERING, "s1", "c"),
    ]
    result = d.dispatch_all(signals)
    s = result.summary()
    assert "dispatched 3" in s
    assert "harness" in s.lower() or "Bucket.HARNESS" in s


def test_full_pipeline_classify_then_dispatch(tmp_path: Path):
    """End-to-end: real text → classifier → dispatcher → JSONL queue."""
    text = """
    [admiral] retry_after_error:search_replace fired 4x
    [admiral] loop:bash with cat << 'EOF' heredoc
    [admiral] empty_after_tool:ralph_file_summary causing stalls
    """
    signals = classify_text(text)
    assert signals
    d = Dispatcher(queue_root=tmp_path)
    result = d.dispatch_all(signals)
    assert result.dispatched >= 3
    harness_jsonl = _queue_path_for(Bucket.HARNESS, tmp_path)
    assert harness_jsonl.is_file()
    lines = harness_jsonl.read_text().splitlines()
    assert len(lines) >= 3


def test_make_jsonl_handler_appends_not_overwrites(tmp_path: Path):
    h = make_jsonl_handler(Bucket.HARNESS, tmp_path)
    h(_signal(Bucket.HARNESS, "h1", "first"))
    h(_signal(Bucket.HARNESS, "h2", "second"))
    path = _queue_path_for(Bucket.HARNESS, tmp_path)
    lines = path.read_text().splitlines()
    assert len(lines) == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

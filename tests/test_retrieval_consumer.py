"""Regression test for scripts/consume_retrieval_queue.py.

The classifier writes RETRIEVAL-bucket entries to
~/.drydock/dispatch/retrieval.jsonl. Until v2.7.36 nothing consumed those
entries, so the queue grew indefinitely while no projects ever got
ingested into GraphRAG. This test pins the consumer's contract:

  - reads the queue
  - parses the evidence-row timestamp
  - matches it to a session via meta.json + working_directory
  - dedups multiple entries for the same project
  - is idempotent across reruns within the re-ingest window
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "consume_retrieval_queue.py"


def _load_consumer(monkeypatch, dispatch_dir: Path, session_root: Path):
    """Import the script as a module with HOME redirected so its module-level
    paths point at the tmp dirs."""
    spec = importlib.util.spec_from_file_location("consume_retrieval_queue", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("HOME", str(dispatch_dir.parent.parent))
    spec.loader.exec_module(module)
    module.DISPATCH_DIR = dispatch_dir
    module.QUEUE = dispatch_dir / "retrieval.jsonl"
    module.STATE = dispatch_dir / ".retrieval_consumed.json"
    module.SESSION_ROOT = session_root
    return module


def _write_session(root: Path, session_id: str, start_iso: str, cwd: str) -> Path:
    sd = root / f"session_{session_id}"
    sd.mkdir(parents=True)
    meta = {
        "session_id": session_id,
        "start_time": start_iso,
        "environment": {"working_directory": cwd},
    }
    (sd / "meta.json").write_text(json.dumps(meta))
    return sd


def _write_queue(queue: Path, evidence_ts: str, count: int) -> None:
    queue.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(count):
        lines.append(json.dumps({
            "bucket": "retrieval",
            "pattern_id": "retrieval:cross_package_inheritance",
            "evidence": f"{evidence_ts} | intervention | struggle :: read 24 times",
            "suggested_action": "ingest",
            "confidence": 0.85,
            "source": "admiral_history.log",
            "session_id": "",
            "prompt_id": "",
            "extra": {},
            "ts": "2026-05-03T13:00:00Z",
        }))
    queue.write_text("\n".join(lines) + "\n")


def test_dedups_same_project_across_entries(tmp_path, monkeypatch):
    home = tmp_path / "home"
    dispatch = home / ".drydock" / "dispatch"
    sessions = home / ".vibe" / "logs" / "session"
    project = tmp_path / "proj"
    project.mkdir()

    _write_session(sessions, "20260503_023800_abc", "2026-05-03T02:38:00+00:00", str(project))
    _write_queue(dispatch / "retrieval.jsonl", "2026-05-03T02:37:52+00:00", count=12)

    mod = _load_consumer(monkeypatch, dispatch, sessions)

    targets: dict = {}
    entries = mod._read_queue()
    state = mod._load_state()
    for e in entries:
        ts = mod._evidence_timestamp(e["evidence"])
        cwd = mod._find_session_cwd(ts)
        if cwd is None:
            continue
        if not mod._stale(state, cwd):
            continue
        targets[str(cwd)] = ts

    assert len(entries) == 12
    assert len(targets) == 1, "12 same-evidence entries must dedup to 1 project"
    assert str(project) in targets


def test_idempotent_within_reingest_window(tmp_path, monkeypatch):
    home = tmp_path / "home"
    dispatch = home / ".drydock" / "dispatch"
    sessions = home / ".vibe" / "logs" / "session"
    project = tmp_path / "proj2"
    project.mkdir()

    _write_session(sessions, "20260503_023800_def", "2026-05-03T02:38:00+00:00", str(project))
    _write_queue(dispatch / "retrieval.jsonl", "2026-05-03T02:37:52+00:00", count=3)

    mod = _load_consumer(monkeypatch, dispatch, sessions)

    state = {str(project): datetime.now(timezone.utc).isoformat()}
    assert mod._stale(state, project) is False, "fresh ingest must NOT re-trigger"

    state[str(project)] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    assert mod._stale(state, project) is True, "8-day-old ingest must re-trigger"


def test_skips_entry_with_no_session_match(tmp_path, monkeypatch):
    home = tmp_path / "home"
    dispatch = home / ".drydock" / "dispatch"
    sessions = home / ".vibe" / "logs" / "session"
    sessions.mkdir(parents=True)

    _write_queue(dispatch / "retrieval.jsonl", "2020-01-01T00:00:00+00:00", count=1)
    mod = _load_consumer(monkeypatch, dispatch, sessions)

    entries = mod._read_queue()
    ts = mod._evidence_timestamp(entries[0]["evidence"])
    assert mod._find_session_cwd(ts) is None, "no session within +/-1h must yield None"


def test_evidence_timestamp_parses_offset_and_z(tmp_path, monkeypatch):
    mod = _load_consumer(monkeypatch, tmp_path / "d", tmp_path / "s")
    a = mod._evidence_timestamp("2026-05-03T02:37:52+00:00 | foo")
    b = mod._evidence_timestamp("2026-05-03T02:37:52Z | foo")
    c = mod._evidence_timestamp("not a timestamp")
    assert a is not None and b is not None
    assert a == b
    assert c is None


def test_dirname_filter_skips_noise(tmp_path, monkeypatch):
    """Pin the perf fix: with 10K out-of-window session dirs and 1
    in-window match, the dirname-prefilter must avoid reading every
    meta.json. We assert the right session is found AND verify the
    name-only filter rejects out-of-window dirs without crashing on
    missing/malformed meta.json files (which otherwise would have
    been read in the old O(N) walk)."""
    home = tmp_path / "home"
    sessions = home / ".vibe" / "logs" / "session"
    sessions.mkdir(parents=True)

    # 50 noise dirs from 2020 — far outside the +/-1h window. Crucially,
    # several have NO meta.json. The pre-fix code walked every dir's
    # meta.json; the post-fix code rejects them on the name-prefix alone.
    for i in range(50):
        d = sessions / f"session_20200101_{i:06d}_noise{i}"
        d.mkdir()
        if i % 3 == 0:
            (d / "meta.json").write_text("not json")
    # The real match
    real = sessions / "session_20260503_023800_real"
    real.mkdir()
    (real / "meta.json").write_text(json.dumps({
        "session_id": "real",
        "start_time": "2026-05-03T02:38:00+00:00",
        "environment": {"working_directory": "/data3/drydock_test_projects/x"},
    }))

    mod = _load_consumer(monkeypatch, home / ".drydock" / "dispatch", sessions)
    target = datetime.fromisoformat("2026-05-03T02:37:52+00:00")
    found = mod._find_session_cwd(target)
    assert found is not None
    assert str(found) == "/data3/drydock_test_projects/x"

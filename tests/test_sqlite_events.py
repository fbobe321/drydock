"""Tests for the SQLite event store (PRD Epic P) — a drop-in for the JSONL
EventLog, append-only, queryable, and readable by summarize()/reconstruct."""
from __future__ import annotations

from drydock.events import (
    SQLiteEventLog,
    make_event_log,
    read_events,
    reconstruct_task_state,
    summarize,
)


def test_emit_and_read_roundtrip(tmp_path):
    db = tmp_path / "trace.db"
    log = SQLiteEventLog(db)
    log.emit("task_start", objective="fix the bug", acceptance_criteria=["x passes"])
    log.emit("turn", in_tok=100, out_tok=20)
    log.emit("tool", name="Bash", status="ok")
    evs = SQLiteEventLog.read(db)
    assert [e["type"] for e in evs] == ["task_start", "turn", "tool"]
    assert [e["seq"] for e in evs] == [1, 2, 3]           # monotonic seq
    assert evs[0]["objective"] == "fix the bug"           # data flattened back
    assert evs[1]["in_tok"] == 100


def test_append_only_across_reopen(tmp_path):
    # PRD P1.2: prior events are not modified; a reopened log continues the seq.
    db = tmp_path / "t.db"
    a = SQLiteEventLog(db)
    a.emit("task_start", objective="o")
    a.emit("turn")
    b = SQLiteEventLog(db)      # reopen
    b.emit("done", phase="complete")
    evs = SQLiteEventLog.read(db)
    assert [e["seq"] for e in evs] == [1, 2, 3]
    assert evs[-1]["type"] == "done"


def test_read_events_dispatches_by_extension(tmp_path):
    db = tmp_path / "x.sqlite"
    log = SQLiteEventLog(db)
    log.emit("task_start", objective="hi")
    assert read_events(db)[0]["objective"] == "hi"


def test_summarize_works_over_sqlite(tmp_path):
    db = tmp_path / "s.db"
    log = SQLiteEventLog(db)
    log.emit("task_start", objective="do X", acceptance_criteria=["a"])
    log.emit("turn", in_tok=10, out_tok=5)
    log.emit("tool", name="Read")
    log.emit("verification", status="pass")
    log.emit("done", phase="complete")
    d = summarize(db)
    assert d["objective"] == "do X"
    assert d["turns"] == 1
    assert d["tools"] == {"Read": 1}
    assert d["verifications"]["pass"] == 1
    assert d["final_phase"] == "complete"


def test_reconstruct_task_state_over_sqlite(tmp_path):
    db = tmp_path / "r.db"
    log = SQLiteEventLog(db)
    log.emit("task_start", objective="build a thing", acceptance_criteria=["c1", "c2"])
    ts = reconstruct_task_state(db)
    assert ts.objective == "build a thing"
    assert ts.acceptance_criteria == ["c1", "c2"]


def test_make_event_log_picks_backend(tmp_path):
    assert isinstance(make_event_log(tmp_path / "a.db"), SQLiteEventLog)
    assert isinstance(make_event_log(tmp_path / "a.jsonl", backend="sqlite"), SQLiteEventLog)
    from drydock.events import EventLog
    assert isinstance(make_event_log(tmp_path / "a.jsonl"), EventLog)


def test_missing_db_reads_empty(tmp_path):
    assert SQLiteEventLog.read(tmp_path / "nope.db") == []


def test_emit_never_raises_on_unserializable(tmp_path):
    log = SQLiteEventLog(tmp_path / "u.db")
    log.emit("weird", obj=object())  # not JSON-serializable -> default=str, no raise
    evs = SQLiteEventLog.read(tmp_path / "u.db")
    assert evs and evs[0]["type"] == "weird"

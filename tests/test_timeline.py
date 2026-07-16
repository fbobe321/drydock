"""Tests for the /trace event timeline formatter."""
from __future__ import annotations

from drydock.events import EventLog, format_timeline


def _log(tmp_path):
    ev = EventLog(tmp_path / "t.jsonl")
    ev.emit("task_start", objective="fix the parser")
    ev.emit("turn", in_tok=100, out_tok=20)
    ev.emit("tool_started", name="Bash", effect="local_mutation")
    ev.emit("tool", name="Bash", status="ok")
    ev.emit("progress", score=-2, streak=3)
    ev.emit("recovery", stage=3, suppressed=True, terminate=False)
    ev.emit("done", phase="complete")
    return ev


def test_timeline_renders_each_event(tmp_path):
    lines = format_timeline(_log(tmp_path).path)
    blob = "\n".join(lines)
    assert "objective: fix the parser" in blob
    assert "▶ Bash" in blob and "✓ Bash" in blob
    assert "progress -2" in blob and "streak 3" in blob
    assert "recovery stage 3" in blob and "SUPPRESS" in blob
    assert "done" in blob and "complete" in blob


def test_timeline_respects_limit(tmp_path):
    ev = EventLog(tmp_path / "t.jsonl")
    for i in range(50):
        ev.emit("turn", in_tok=i, out_tok=i)
    lines = format_timeline(ev.path, limit=10)
    assert any("earlier events omitted" in ln for ln in lines)
    # 1 omission header + 10 events
    assert len(lines) == 11


def test_empty_timeline(tmp_path):
    ev = EventLog(tmp_path / "empty.jsonl")
    assert format_timeline(ev.path) == []

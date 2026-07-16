"""Tests for resume reconstruction (PRD P2.1/P2.2/P2.3): rebuild a session from a
snapshot + event trace, flag in-flight external mutations as unsafe to retry."""
from __future__ import annotations

from drydock import resume
from drydock.agent import AgentState
from drydock.events import EventLog
from drydock.task_state import TaskState


def _snapshot(tmp_path, messages, objective="build a thing"):
    st = AgentState()
    st.task = TaskState.from_objective(objective)
    st.messages = messages
    st.turn_count = 5
    st.budget.session_turns = 5
    st.budget.session_tool_calls = 9
    path = tmp_path / "s.json"
    resume.save_snapshot(st, {"model": "gemma4", "cwd": "/repo", "session_id": "sess"}, path)
    return path


def test_restore_rebuilds_task_and_transcript(tmp_path):
    path = _snapshot(tmp_path, [{"role": "user", "content": "build a thing"}])
    info = resume.restore(path)
    assert info is not None
    assert info.objective == "build a thing"
    assert info.model == "gemma4"
    assert info.messages[0]["content"] == "build a thing"
    assert info.turn_count == 5


def test_restore_none_for_missing(tmp_path):
    assert resume.restore(tmp_path / "nope.json") is None


def test_unresolved_readonly_is_safe(tmp_path):
    path = _snapshot(tmp_path, [{"role": "user", "content": "x"}])
    ev = EventLog(tmp_path / "t.jsonl")
    ev.emit("tool_started", name="Read", effect="read_only")  # interrupted
    info = resume.restore(path, ev.path)
    assert len(info.unresolved) == 1
    assert info.has_unsafe_unresolved is False
    assert any("safe to redo" in w for w in info.warnings)


def test_unresolved_external_mutation_is_unsafe(tmp_path):
    # PRD P2.3: an interrupted external mutation must be flagged, not auto-retried.
    path = _snapshot(tmp_path, [{"role": "user", "content": "x"}])
    ev = EventLog(tmp_path / "t.jsonl")
    ev.emit("tool_started", name="mcp__github__create_issue", effect="external_mutation")
    info = resume.restore(path, ev.path)
    assert info.has_unsafe_unresolved is True
    assert any("verify before retrying" in w for w in info.warnings)


def test_apply_to_state_restores_session(tmp_path):
    path = _snapshot(tmp_path, [{"role": "user", "content": "build a thing"},
                                {"role": "assistant", "content": "working"}])
    info = resume.restore(path)
    st = AgentState()
    resume.apply_to_state(info, st)
    assert st.task.objective == "build a thing"
    assert len(st.messages) == 2
    assert st.turn_count == 5
    assert st.budget.session_turns == 5
    assert st.budget.session_tool_calls == 9


def test_resume_note_mentions_inflight(tmp_path):
    path = _snapshot(tmp_path, [{"role": "user", "content": "x"}])
    ev = EventLog(tmp_path / "t.jsonl")
    ev.emit("tool_started", name="Bash", effect="local_mutation")
    info = resume.restore(path, ev.path)
    note = resume.resume_note(info)
    assert "RESUMING" in note and "Bash" in note

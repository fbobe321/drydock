"""Tests for durable session snapshots (PRD P2.1): atomic per-turn save, clear on
clean completion, and end-to-end via the agent loop."""
from __future__ import annotations

import tempfile

import drydock.agent as agent_mod
from drydock import resume
from drydock.agent import AgentState, run
from drydock.providers import AssistantTurn
from drydock.task_state import TaskState


def test_save_and_load_roundtrip(tmp_path):
    st = AgentState()
    st.task = TaskState.from_objective("fix the parser")
    st.messages = [{"role": "user", "content": "fix the parser"},
                   {"role": "assistant", "content": "on it"}]
    st.turn_count = 3
    path = tmp_path / "s.json"
    assert resume.save_snapshot(st, {"model": "gemma4", "cwd": "/repo"}, path) == path
    snap = resume.load_snapshot(path)
    assert snap["task"]["objective"] == "fix the parser"
    assert snap["messages"][0]["content"] == "fix the parser"
    assert snap["model"] == "gemma4" and snap["turn_count"] == 3


def test_clear_removes_snapshot(tmp_path):
    st = AgentState()
    path = tmp_path / "s.json"
    resume.save_snapshot(st, {}, path)
    assert path.exists()
    resume.clear_snapshot(path)
    assert not path.exists()


def test_save_is_atomic_no_tmp_left(tmp_path):
    st = AgentState()
    path = tmp_path / "s.json"
    resume.save_snapshot(st, {}, path)
    assert not (tmp_path / "s.json.tmp").exists()  # temp renamed away


def test_load_missing_is_none(tmp_path):
    assert resume.load_snapshot(tmp_path / "nope.json") is None


def test_loop_writes_snapshot_each_turn_and_clears_on_done(tmp_path, monkeypatch):
    path = tmp_path / "sess.json"
    turns = [
        AssistantTurn("", [{"id": "1", "name": "Bash",
                            "input": {"command": "echo hi"}}], 1, 1),
        AssistantTurn("all done", [], 1, 1),
    ]
    # capture whether the snapshot existed mid-run (after the first turn)
    seen = {}
    real_save = resume.save_snapshot

    def spy(state, config, p):
        r = real_save(state, config, p)
        seen["existed"] = tmp_path.joinpath("sess.json").exists()
        return r
    monkeypatch.setattr(resume, "save_snapshot", spy)
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([turns.pop(0)]))
    st = AgentState()
    list(run("go", st, {"model": "m", "cwd": tempfile.mkdtemp(),
                        "verify_gate": False, "resume_path": str(path)}, "sys"))
    assert seen.get("existed") is True    # snapshot was written mid-run
    assert not path.exists()              # cleared on clean completion


def test_interrupted_run_leaves_resumable_snapshot(tmp_path, monkeypatch):
    # A run stopped short of completion (max_turns) leaves its snapshot behind so
    # it can be resumed; restore() rebuilds the objective + transcript.
    from drydock.events import EventLog
    path = tmp_path / "sess.json"
    ev = EventLog(tmp_path / "e.jsonl")

    def never_done(**kw):
        return iter([AssistantTurn(
            "", [{"id": "1", "name": "Bash", "input": {"command": "echo step"}}], 1, 1)])
    monkeypatch.setattr(agent_mod, "stream", never_done)
    st = AgentState()
    st.events = ev
    list(run("fix the CSV bug", st, {"model": "m", "cwd": tempfile.mkdtemp(),
             "verify_gate": False, "resume_path": str(path), "max_turns": 2}, "sys"))
    assert path.exists()  # interrupted (hit max_turns) -> snapshot survives
    info = resume.restore(path)
    assert info.objective == "fix the CSV bug"
    assert len(info.messages) >= 1


def test_list_and_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(resume, "snapshot_dir", lambda: tmp_path)
    st = AgentState()
    resume.save_snapshot(st, {"session_id": "a"}, tmp_path / "a.json")
    resume.save_snapshot(st, {"session_id": "b"}, tmp_path / "b.json")
    snaps = resume.list_snapshots()
    assert len(snaps) == 2
    assert resume.latest_snapshot() in snaps

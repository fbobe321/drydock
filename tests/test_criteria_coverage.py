"""Tests for criteria-tied verification (task #22 / bench finding: exit-0
self-checks that never touch the files the task is judged on). Advisory: one
bounded nudge naming the uncovered artifacts, then completion proceeds."""
from __future__ import annotations

import tempfile

import drydock.agent as agent_mod
from drydock.agent import AgentState, run
from drydock.providers import AssistantTurn
from drydock.verification import extract_artifacts, uncovered_artifacts

LSTE_OBJECTIVE = (
    "Transform /app/input.csv (1 million rows) to match /app/expected.csv exactly "
    "using keystroke-efficient Vim macros. Save your script as /app/apply_macros.vim."
)


def test_extract_artifacts_from_objective():
    arts = extract_artifacts(LSTE_OBJECTIVE)
    assert "input.csv" in arts
    assert "expected.csv" in arts
    assert "apply_macros.vim" in arts


def test_extract_ignores_prose_dots():
    arts = extract_artifacts("Fix the bug, e.g. the crash in v1.2 — i.e. the parser. No.1 priority.")
    assert arts == []


def test_uncovered_artifacts():
    arts = ["input.csv", "expected.csv", "apply_macros.vim"]
    checks = "cp /app/test_input.csv t.csv\nvim -Es t.csv -S /app/apply_macros.vim\n[ok]"
    missing = uncovered_artifacts(arts, checks)
    assert missing == ["expected.csv"]  # never diffed — the LSTE blind spot
    assert uncovered_artifacts([], "anything") == []


def _write(path, content="x"):
    return {"name": "Write", "input": {"file_path": path, "content": content}}


def test_uncovered_check_gets_one_nudge_then_covered_completes(monkeypatch):
    d = tempfile.mkdtemp()
    with open(f"{d}/expected.csv", "w") as f:
        f.write("A\n")
    turns = [
        AssistantTurn("", [dict(id="1", **_write(f"{d}/out.csv", "A\n"))], 1, 1),
        # verification that touches NOTHING named in the objective
        AssistantTurn("", [{"id": "2", "name": "Bash",
                            "input": {"command": "python3 -c 'print(1)'"}}], 1, 1),
        AssistantTurn("done", [], 1, 1),          # claim -> should get coverage nudge
        # now a check that touches expected.csv
        AssistantTurn("", [{"id": "3", "name": "Bash",
                            "input": {"command": f"python3 -c 'print(open(\"{d}/expected.csv\").read())'"}}], 1, 1),
        AssistantTurn("done for real", [], 1, 1),  # accepted
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([turns.pop(0)]))
    st = AgentState()
    list(run(f"Make out.csv match {d}/expected.csv exactly.", st,
             {"model": "m", "cwd": d}, "sys"))
    nudges = [m["content"] for m in st.messages
              if m.get("role") == "user" and "has touched" in str(m.get("content"))]
    assert len(nudges) == 1
    assert "expected.csv" in nudges[0]
    assert any(m.get("content") == "done for real" for m in st.messages)
    assert str(st.task.phase) == "complete"


def test_nudge_is_bounded_second_claim_accepted(monkeypatch):
    # The model ignores the nudge and claims done again -> advisory, accepted.
    d = tempfile.mkdtemp()
    turns = [
        AssistantTurn("", [dict(id="1", **_write(f"{d}/a.py"))], 1, 1),
        AssistantTurn("", [{"id": "2", "name": "Bash",
                            "input": {"command": "python3 -c 'print(1)'"}}], 1, 1),
        AssistantTurn("done", [], 1, 1),     # nudged (goal.csv never touched)
        AssistantTurn("still done", [], 1, 1),  # accepted anyway
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([turns.pop(0)]))
    st = AgentState()
    list(run("Produce goal.csv from the data.", st, {"model": "m", "cwd": d}, "sys"))
    assert any(m.get("content") == "still done" for m in st.messages)
    assert str(st.task.phase) == "complete"


def test_no_artifacts_in_objective_no_nudge(monkeypatch):
    # An objective naming no files behaves exactly as before: pass -> complete.
    d = tempfile.mkdtemp()
    turns = [
        AssistantTurn("", [dict(id="1", **_write(f"{d}/x.py", "print(1)"))], 1, 1),
        AssistantTurn("", [{"id": "2", "name": "Bash",
                            "input": {"command": f"python3 {d}/x.py"}}], 1, 1),
        AssistantTurn("all done", [], 1, 1),
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([turns.pop(0)]))
    st = AgentState()
    list(run("write a script that prints 1 and run it", st, {"model": "m", "cwd": d}, "sys"))
    nudges = [m for m in st.messages
              if m.get("role") == "user" and "has touched" in str(m.get("content"))]
    assert nudges == []
    assert str(st.task.phase) == "complete"


def test_covered_checks_complete_without_nudge(monkeypatch):
    # Checks that DO touch the named artifact -> no nudge at all.
    d = tempfile.mkdtemp()
    with open(f"{d}/expected.csv", "w") as f:
        f.write("A\n")
    turns = [
        AssistantTurn("", [dict(id="1", **_write(f"{d}/out.csv", "A\n"))], 1, 1),
        AssistantTurn("", [{"id": "2", "name": "Bash",
                            "input": {"command": f"python3 -c 'print(open(\"{d}/expected.csv\").read() == open(\"{d}/out.csv\").read())'"}}], 1, 1),
        AssistantTurn("matches", [], 1, 1),
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([turns.pop(0)]))
    st = AgentState()
    list(run(f"Make out.csv match {d}/expected.csv exactly.", st, {"model": "m", "cwd": d}, "sys"))
    nudges = [m for m in st.messages
              if m.get("role") == "user" and "has touched" in str(m.get("content"))]
    assert nudges == []
    assert str(st.task.phase) == "complete"
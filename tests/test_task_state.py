"""Structured task state (Agent-Buildout PRD Epic A): the objective + acceptance
criteria live outside the transcript, are serializable, and survive compaction."""
from __future__ import annotations

from drydock import agent
from drydock.providers import AssistantTurn
from drydock.task_state import TaskState, extract_acceptance_criteria


def test_extracts_numbered_and_bulleted_criteria():
    obj = "Build a parser. Requirements:\n1. Read /app/in.txt\n2. Write /app/out.json\n- Exit 0"
    c = extract_acceptance_criteria(obj)
    assert c == ["Read /app/in.txt", "Write /app/out.json", "Exit 0"]


def test_extracts_imperative_when_no_list():
    c = extract_acceptance_criteria("The script must print the CN and it should exit 0.")
    assert any("must print" in x for x in c)


def test_from_objective_and_serializable_without_transcript():
    ts = TaskState.from_objective("Do X.\n1. A\n2. B")
    assert ts.is_set() and ts.acceptance_criteria == ["A", "B"] and ts.phase == "understand"
    d = ts.to_dict()                      # A1.1: serializable without the transcript
    assert d["objective"] and d["acceptance_criteria"] == ["A", "B"]
    assert TaskState.from_dict(d).objective == ts.objective   # reconstructable


def test_anchor_text_includes_objective_and_criteria():
    a = TaskState.from_objective("Fix the bug.\n1. tests pass\n2. no regressions").anchor_text()
    assert "Fix the bug" in a and "tests pass" in a and "no regressions" in a
    assert TaskState().anchor_text() == ""   # empty when unset


def _run_capture(cfg, msg, state=None):
    seen = {}
    def fake_stream(model, system, messages, tool_schemas, config):
        seen["system"] = system
        yield AssistantTurn("done", [], 5, 5)
    orig = agent.stream; agent.stream = fake_stream
    try:
        st = state or agent.AgentState()
        list(agent.run(msg, st, cfg, "BASE"))
        return seen["system"], st
    finally:
        agent.stream = orig


def test_objective_captured_and_anchored_in_system_prompt():
    cfg = {"model": "gemma4", "max_tokens": 8192, "context_limit": 65536, "task_anchor": True}
    sysp, st = _run_capture(cfg, "Recover the password.\n1. it is 23 chars\n2. write it to out.txt")
    assert st.task.is_set() and st.task.acceptance_criteria == ["it is 23 chars", "write it to out.txt"]
    assert "TASK" in sysp and "23 chars" in sysp   # anchor present in system prompt


def test_anchor_survives_a_compacted_follow_up_turn():
    # A2: after the transcript is replaced (compaction), the objective is still in the
    # system prompt on the next turn because it lives in TaskState, not the messages.
    cfg = {"model": "gemma4", "max_tokens": 8192, "context_limit": 65536, "task_anchor": True}
    _, st = _run_capture(cfg, "Original objective X.\n1. criterion one")
    st.messages = [{"role": "user", "content": "(transcript summarized away)"}]  # simulate compaction
    sysp2, _ = _run_capture(cfg, "keep going", state=st)
    assert "Original objective X" in sysp2 and "criterion one" in sysp2


def test_disabled_when_task_anchor_false():
    cfg = {"model": "gemma4", "max_tokens": 8192, "context_limit": 65536, "task_anchor": False}
    sysp, _ = _run_capture(cfg, "Do the thing.\n1. a criterion")
    assert "TASK" not in sysp


def test_phase_transitions_understand_implement_complete():
    from drydock.providers import AssistantTurn
    cfg = {"model": "gemma4", "max_tokens": 8192, "context_limit": 65536}
    turns = [
        AssistantTurn("", [{"id": "1", "name": "Write",
                            "input": {"file_path": "/tmp/dd_ph.txt", "content": "x"}}], 5, 5),
        AssistantTurn("", [{"id": "2", "name": "Bash", "input": {"command": "python -c 'print(1)'"}}], 5, 5),
        AssistantTurn("done, verified", [], 5, 5),
    ]
    i = {"n": 0}
    def fake(model, system, messages, tool_schemas, config):
        t = turns[min(i["n"], len(turns) - 1)]; i["n"] += 1; yield t
    st = agent.AgentState()
    assert st.task.phase == "understand"
    orig = agent.stream; agent.stream = fake
    try:
        list(agent.run("Fix it and verify.\n1. tests pass", st, cfg, "SYS"))
    finally:
        agent.stream = orig
    assert st.task.phase == "complete"   # understand -> implement -> verify -> complete

"""Verification gating (Agent-Buildout PRD Epic B): a text-only 'done' after editing
files is not accepted until the agent runs a check. Model can't self-declare done."""
from __future__ import annotations

from drydock import agent
from drydock.providers import AssistantTurn
from drydock.verification import looks_like_verification as v


def test_classifier():
    for cmd in ("pytest -q", "python -m pytest tests/", "bash test.sh", "make test",
                "npm test", "cargo test", "ruff check .", "mypy src", "python solve.py",
                "./run_check", "node index.js"):
        assert v(cmd), cmd
    for cmd in ("ls -la", "cat file.txt", "grep foo bar", "echo hi", "git status", ""):
        assert not v(cmd), cmd
    assert not v(None) and not v(123)


def _drive(cfg, turns):
    """turns: list of AssistantTurn to yield in order; returns the messages seen."""
    calls = {"i": 0}
    def fake_stream(model, system, messages, tool_schemas, config):
        i = calls["i"]; calls["i"] += 1
        yield turns[min(i, len(turns) - 1)]
    orig = agent.stream; agent.stream = fake_stream
    try:
        st = agent.AgentState()
        list(agent.run("do it", st, cfg, "SYS"))
        return st, calls["i"]
    finally:
        agent.stream = orig


CFG = {"model": "gemma4", "max_tokens": 8192, "context_limit": 65536, "verify_gate": True}


def test_gate_fires_after_edit_without_verification():
    # turn 1: Write a file (no tool result needed — execute() runs the real tool);
    # turn 2: text-only "done". Gate should nudge -> a 3rd call happens.
    write = AssistantTurn("", [{"id": "1", "name": "Write",
                                "input": {"file_path": "/tmp/dd_vg.txt", "content": "x"}}], 5, 5)
    done = AssistantTurn("All done!", [], 5, 5)
    st, n_calls = _drive(CFG, [write, done, done])
    # the gate injected a verify nudge → the loop asked the model again (>2 calls)
    assert n_calls >= 3
    assert any("VERIFIED" in m.get("content", "") for m in st.messages if m.get("role") == "user")


def test_no_gate_when_verification_ran():
    write = AssistantTurn("", [{"id": "1", "name": "Write",
                                "input": {"file_path": "/tmp/dd_vg2.txt", "content": "x"}}], 5, 5)
    check = AssistantTurn("", [{"id": "2", "name": "Bash", "input": {"command": "python -c 'print(1)'"}}], 5, 5)
    done = AssistantTurn("Done, verified.", [], 5, 5)
    st, _ = _drive(CFG, [write, check, done])
    assert not any("VERIFIED" in m.get("content", "") for m in st.messages if m.get("role") == "user")
    assert st.task.phase == "complete"


def test_no_gate_when_no_edits():
    # pure Q&A: no files changed → nothing to verify → accept immediately
    st, _ = _drive(CFG, [AssistantTurn("The answer is 42.", [], 5, 5)])
    assert not any("VERIFIED" in m.get("content", "") for m in st.messages if m.get("role") == "user")


def test_disabled():
    write = AssistantTurn("", [{"id": "1", "name": "Write",
                                "input": {"file_path": "/tmp/dd_vg3.txt", "content": "x"}}], 5, 5)
    done = AssistantTurn("done", [], 5, 5)
    st, _ = _drive({**CFG, "verify_gate": False}, [write, done])
    assert not any("VERIFIED" in m.get("content", "") for m in st.messages if m.get("role") == "user")


def test_evidence_pass_fail():
    from drydock.verification import parse_evidence
    assert parse_evidence("pytest", "5 passed in 0.1s").status == "pass"
    assert parse_evidence("pytest", "2 failed, 3 passed\n\n[exit code: 1]").status == "fail"
    assert parse_evidence("python x.py", "boom\n\n[exit code: 1]").status == "fail"
    ev = parse_evidence("pytest", "2 failed, 3 passed\n\n[exit code: 1]")
    assert ev.tests_passed == 3 and ev.tests_failed == 2 and ev.exit_code == 1


def test_failed_check_triggers_repair_not_complete():
    # write, then run a FAILING check, then claim done -> gate nudges to REPAIR
    write = AssistantTurn("", [{"id": "1", "name": "Write",
                                "input": {"file_path": "/tmp/dd_vg_f.txt", "content": "x"}}], 5, 5)
    fail = AssistantTurn("", [{"id": "2", "name": "Bash",
                              "input": {"command": "python -c 'import sys; sys.exit(1)'"}}], 5, 5)
    done = AssistantTurn("all done", [], 5, 5)
    st, n = _drive(CFG, [write, fail, done, done])
    assert st.task.phase == "repair"
    assert any("FAILED" in m.get("content", "") for m in st.messages if m.get("role") == "user")


def test_passing_check_accepts_completion():
    write = AssistantTurn("", [{"id": "1", "name": "Write",
                                "input": {"file_path": "/tmp/dd_vg_p.txt", "content": "x"}}], 5, 5)
    ok = AssistantTurn("", [{"id": "2", "name": "Bash",
                            "input": {"command": "python -c 'print(1)'"}}], 5, 5)
    done = AssistantTurn("done, verified", [], 5, 5)
    st, _ = _drive(CFG, [write, ok, done])
    assert st.task.phase == "complete"
    assert not any("FAILED" in m.get("content", "") for m in st.messages if m.get("role") == "user")

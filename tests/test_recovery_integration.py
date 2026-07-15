"""End-to-end proof that Epic K catches the large-scale-text-editing failure mode:
an ALTERNATING write-macro -> run-failing-test loop. This evades the existing
consecutive-identical safety valve (the calls alternate, so no call repeats
back-to-back), and the identical re-write reports changed_state=True — yet the
progress window + recovery escalation must still stop it well before max_turns."""
from __future__ import annotations

import tempfile

import drydock.agent as agent_mod
from drydock.agent import AgentState, run
from drydock.providers import AssistantTurn


def _alternating_model(macro_path):
    """Model that forever alternates: write the SAME macro, then run the SAME
    failing test. Exactly the observed trajectory shape."""
    def stream(**kw):
        # even turns: rewrite identical macro; odd turns: run identical failing test
        n = stream.n
        stream.n += 1
        if n % 2 == 0:
            tc = {"id": str(n), "name": "Write",
                  "input": {"file_path": macro_path, "content": "call setreg('a', \"gUU\")\n"}}
        else:
            tc = {"id": str(n), "name": "Bash",
                  "input": {"command": "false  # the failing test, unchanged"}}
        return iter([AssistantTurn("", [tc], 1, 1)])
    stream.n = 0
    return stream


def test_alternating_write_test_loop_is_stopped_before_max_turns(monkeypatch):
    macro = tempfile.NamedTemporaryFile(suffix=".vim", delete=False).name
    monkeypatch.setattr(agent_mod, "stream", _alternating_model(macro))
    st = AgentState()
    cfg = {"model": "m", "cwd": tempfile.mkdtemp(),
           "verify_gate": False, "max_turns": 200}
    list(run("transform the csv with a vim macro", st, cfg, "sys"))
    # Recovery must have ended the run far short of the 200-turn ceiling — the old
    # behavior (this exact loop) ran to the cap because it evades the identical
    # consecutive valve.
    assert st.turn_count < 40, f"loop ran {st.turn_count} turns — recovery didn't fire"


def test_governor_state_is_surfaced_on_agent_state(monkeypatch):
    # The TUI reads state.recovery_stage / state.progress_streak to show the
    # governor working. A fresh state starts clean; the alternating loop drives
    # the stage up.
    st = AgentState()
    assert st.recovery_stage == 0 and st.progress_streak == 0
    macro = tempfile.NamedTemporaryFile(suffix=".vim", delete=False).name
    monkeypatch.setattr(agent_mod, "stream", _alternating_model(macro))
    list(run("loop task", st,
             {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False,
              "max_turns": 200}, "sys"))
    # recovery escalated during the stall
    assert st.recovery_stage >= 3


def test_healthy_distinct_work_runs_to_completion(monkeypatch):
    # Guard against over-eager recovery: a model doing DISTINCT productive work
    # and then finishing must not be cut off.
    seq = [
        AssistantTurn("", [{"id": "1", "name": "Write",
                            "input": {"file_path": "a.txt", "content": "1"}}], 1, 1),
        AssistantTurn("", [{"id": "2", "name": "Write",
                            "input": {"file_path": "b.txt", "content": "2"}}], 1, 1),
        AssistantTurn("", [{"id": "3", "name": "Write",
                            "input": {"file_path": "c.txt", "content": "3"}}], 1, 1),
        AssistantTurn("all done", [], 1, 1),
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    list(run("make three files", st,
             {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False}, "sys"))
    assert any(m.get("content") == "all done" for m in st.messages)

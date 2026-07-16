"""Regression for the gov162 large-scale-text-editing finding: the same exact
(call, result) pair recurring NON-consecutively is a loop, even when interleaved
actions look productive (identical macro rewritten 22x between exit-0 test runs
whose +2 scores kept resetting the stall streak). Polling stays exempt."""
from __future__ import annotations

import tempfile

import drydock.agent as agent_mod
from drydock.agent import AgentState, run
from drydock.loop_detect import LoopTracker
from drydock.providers import AssistantTurn


def test_outcome_count_getter():
    t = LoopTracker()
    t.record_outcome("Write", {"file_path": "m.vim", "content": "x"}, "ok")
    t.record_outcome("Write", {"file_path": "m.vim", "content": "x"}, "ok")
    assert t.outcome_count("Write", {"file_path": "m.vim", "content": "x"}, "ok") == 2
    # different result -> different pair
    assert t.outcome_count("Write", {"file_path": "m.vim", "content": "x"}, "other") == 0


def _cycling_model(macro_path):
    """The gov162 pattern: identical Write alternating with VARYING, succeeding
    Bash commands — the interleaved +2s reset the global stall streak, so only
    per-signature cycling detection can catch it."""
    def stream(**kw):
        n = stream.n
        stream.n += 1
        if n % 2 == 0:
            tc = {"id": str(n), "name": "Write",
                  "input": {"file_path": macro_path, "content": "call setreg('a', \"gUU\")\n"}}
        else:
            # a different, harmless, succeeding command each time
            tc = {"id": str(n), "name": "Bash",
                  "input": {"command": f"echo run {n} && touch t{n}.txt"}}
        return iter([AssistantTurn("", [tc], 1, 1)])
    stream.n = 0
    return stream


def test_nonconsecutive_identical_pair_gets_suppressed(monkeypatch):
    macro = tempfile.NamedTemporaryFile(suffix=".vim", delete=False).name
    monkeypatch.setattr(agent_mod, "stream", _cycling_model(macro))
    st = AgentState()
    cfg = {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False,
           "max_turns": 40}
    list(run("transform the file with a macro", st, cfg, "sys"))
    # After the same Write+result pair recurs past the threshold, stage 3 fires
    # and the signature is suppressed — visible as the redirect message in a
    # tool result.
    tool_msgs = [m.get("content", "") for m in st.messages if m.get("role") == "tool"]
    assert any("temporarily unavailable" in c for c in tool_msgs), \
        "cycling Write was never suppressed"


def test_polling_with_changing_results_never_suppressed(monkeypatch):
    # Same command, DIFFERENT result each time (a poll): each (call, result)
    # pair is unique, so cycling detection must stay quiet.
    seq = []
    for i in range(10):
        seq.append(AssistantTurn(
            "", [{"id": str(i), "name": "Bash",
                  "input": {"command": "date +%N"}}], 1, 1))  # changing output
    seq.append(AssistantTurn("done polling", [], 1, 1))
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    list(run("watch the clock", st,
             {"model": "m", "cwd": tempfile.mkdtemp(), "verify_gate": False}, "sys"))
    tool_msgs = [m.get("content", "") for m in st.messages if m.get("role") == "tool"]
    assert not any("temporarily unavailable" in c for c in tool_msgs)
    assert any(m.get("content") == "done polling" for m in st.messages)

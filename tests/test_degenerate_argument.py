"""Regression for the full166 build-pmars finding: an ESCALATING-ARGUMENT loop
(mv x_temp -> x_temp_temp -> ...) makes every call unique and every mv look
productive — the amplifying unit inside the argument is the only tell."""
from __future__ import annotations

import tempfile

import drydock.agent as agent_mod
from drydock.agent import AgentState, run
from drydock.loop_detect import degenerate_argument
from drydock.providers import AssistantTurn


def test_detects_the_pmars_spiral():
    path = "/app/pmars_source" + "_temp" * 9
    assert degenerate_argument(f"mv {path} /app/x") == "_temp"


def test_healthy_commands_pass():
    for cmd in (
        "mv /app/pmars_source_temp /app/pmars",     # one _temp is fine
        "python3 -m pytest tests/test_loop_detect.py -q",
        "ls -la /app && cat /app/README.md",
        "grep -rn 'def main' src/",
        "echo hello world",
    ):
        assert degenerate_argument(cmd) is None, cmd


def test_whitespace_units_ignored():
    assert degenerate_argument("echo 'a" + "    " * 20 + "b'") is None


def test_large_nonrepetitive_arg_is_bounded():
    """A big non-repetitive command (heredoc / base64 blob / long one-liner)
    must not cost O(unit^2 * len) — degenerate_argument runs on EVERY tool call.
    The prefix cap keeps it cheap; it just isn't a degenerate arg."""
    import random
    import time
    rng = random.Random(1)
    big = "".join(rng.choice("abcdefghijklmnop/ -_.") for _ in range(50_000))
    t0 = time.perf_counter()
    assert degenerate_argument(big) is None
    assert time.perf_counter() - t0 < 0.2, "scan should be bounded by the prefix cap"


def test_repeat_within_prefix_still_detected_in_long_arg():
    # An escalating unit near the start is still caught even when the command
    # is long — only the tail beyond max_scan is skipped.
    cmd = "mv " + "seg/" * 8 + "x" * 40_000
    assert degenerate_argument(cmd) is not None


def _renaming_spiral(base):
    """Model that alternates mv (arg grows a _temp each time) with ls — the
    exact build-pmars pattern: every command unique, every mv 'productive'."""
    def stream(**kw):
        n = stream.n
        stream.n += 1
        if n % 2 == 0:
            suffix = "_temp" * (4 + n // 2)
            tc = {"id": str(n), "name": "Bash",
                  "input": {"command": f"mv {base} {base}{suffix} 2>/dev/null; true"}}
        else:
            tc = {"id": str(n), "name": "Bash", "input": {"command": f"ls {base} 2>/dev/null; true"}}
        return iter([AssistantTurn("", [tc], 1, 1)])
    stream.n = 0
    return stream


def test_spiral_is_stopped_by_escalation(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setattr(agent_mod, "stream", _renaming_spiral(f"{d}/src"))
    st = AgentState()
    list(run("extract the source and build it", st,
             {"model": "m", "cwd": d, "verify_gate": False, "max_turns": 60}, "sys"))
    # The spiral must be cut well short of the 60-turn ceiling, with the
    # degenerate-argument note visible to the model along the way.
    assert st.turn_count < 40, f"spiral ran {st.turn_count} turns"
    tool_msgs = [m.get("content", "") for m in st.messages if m.get("role") == "tool"]
    assert any("repeated many times" in c for c in tool_msgs)


def test_healthy_distinct_work_unaffected(monkeypatch):
    d = tempfile.mkdtemp()
    seq = [
        AssistantTurn("", [{"id": "1", "name": "Bash",
                            "input": {"command": f"mkdir -p {d}/a && touch {d}/a/x"}}], 1, 1),
        AssistantTurn("", [{"id": "2", "name": "Bash",
                            "input": {"command": f"mv {d}/a {d}/b"}}], 1, 1),
        AssistantTurn("done", [], 1, 1),
    ]
    monkeypatch.setattr(agent_mod, "stream", lambda **kw: iter([seq.pop(0)]))
    st = AgentState()
    list(run("reorganize", st, {"model": "m", "cwd": d, "verify_gate": False}, "sys"))
    assert any(m.get("content") == "done" for m in st.messages)
    tool_msgs = [m.get("content", "") for m in st.messages if m.get("role") == "tool"]
    assert not any("repeated many times" in c for c in tool_msgs)
"""Verified-trajectory export (RSI training-data collection): drydock writes the
full task transcript when trajectory_file is set, and does nothing otherwise."""
from __future__ import annotations

import json
import os
import tempfile

from drydock import agent, trajectory
from drydock.providers import AssistantTurn


def _run(cfg, msg="Do the task.\n1. a criterion"):
    turns = [
        AssistantTurn("", [{"id": "1", "name": "Write",
                            "input": {"file_path": "/tmp/t.py", "content": "x"}}], 10, 5),
        AssistantTurn("", [{"id": "2", "name": "Bash", "input": {"command": "python /tmp/t.py"}}], 8, 4),
        AssistantTurn("done, verified", [], 6, 3),
    ]
    i = {"n": 0}
    def fake(model, system, messages, tool_schemas, config):
        t = turns[min(i["n"], 2)]; i["n"] += 1; yield t
    orig = agent.stream; agent.stream = fake
    try:
        st = agent.AgentState()
        list(agent.run(msg, st, cfg, "SYSTEM"))
        return st
    finally:
        agent.stream = orig


def test_exports_full_trajectory_when_enabled():
    tf = os.path.join(tempfile.mkdtemp(), "traj.json")
    cfg = {"model": "gemma4", "context_limit": 65536, "max_tokens": 8192, "trajectory_file": tf}
    _run(cfg)
    rec = json.load(open(tf))
    assert rec["system"] == "SYSTEM" or rec["system"].startswith("SYSTEM")
    assert rec["objective"].startswith("Do the task")
    assert rec["n_messages"] >= 4 and set(rec["tools"]) == {"Bash", "Write"}
    assert rec["verified"] is None and rec["reward"] is None   # collector stamps these
    assert [m["role"] for m in rec["messages"]][0] == "user"


def test_off_by_default_no_file_no_crash():
    st = _run({"model": "gemma4", "context_limit": 65536, "max_tokens": 8192})
    assert st.messages   # ran fine; nothing exported


def test_record_never_raises_on_bad_path():
    st = agent.AgentState(); st.messages = [{"role": "user", "content": "x"}]
    assert trajectory.record("s", st, {"trajectory_file": "/root/no/perm/x.json"}) is None


def test_build_record_clips_huge_bodies():
    st = agent.AgentState()
    st.messages = [{"role": "tool", "name": "Bash", "content": "A" * 500_000}]
    rec = trajectory.build_record("s", st, {"model": "m"})
    assert len(rec["messages"][0]["content"]) < 250_000 and "truncated" in rec["messages"][0]["content"]

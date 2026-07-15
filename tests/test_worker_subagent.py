"""Worker sub-agent: a WRITABLE sub-agent that does a self-contained task in its own
fresh context and returns only a summary — so its work stays out of the main context.
(task/Dispatch are the read-only investigation variants.)"""
from __future__ import annotations

import os
import tempfile

from drydock import agent, tools, tool_registry
from drydock.tools import WORKER_TOOLS
from drydock.tuning import filter_tool_schemas
from drydock.providers import AssistantTurn

tool_registry  # noqa: keep import (register happens on module import)


def test_registered_writable_and_gemma_available():
    names = [s["name"] for s in tool_registry.schemas()]
    assert "Worker" in names
    assert "Worker" in [s["name"] for s in filter_tool_schemas(tool_registry.schemas(), "gemma4")]
    assert not tool_registry.get("Worker").read_only          # it writes
    assert "Write" in WORKER_TOOLS and "Edit" in WORKER_TOOLS  # can do work
    assert not any(t in WORKER_TOOLS for t in ("Worker", "task", "Dispatch"))  # no recursion


def _fake_subagent(turns):
    i = {"n": 0}
    def fake(model, system, messages, tool_schemas, config):
        t = turns[min(i["n"], len(turns) - 1)]; i["n"] += 1; yield t
    return fake


def test_worker_does_work_and_returns_only_summary():
    d = tempfile.mkdtemp(); target = os.path.join(d, "out.txt")
    turns = [
        AssistantTurn("", [{"id": "1", "name": "Write",
                            "input": {"file_path": target, "content": "done"}}], 5, 5),
        AssistantTurn("Wrote out.txt. Task complete.", [], 5, 5),
    ]
    orig = agent.stream; agent.stream = _fake_subagent(turns)
    try:
        summary = tools.tool_worker({"prompt": "create out.txt"},
                                    {"model": "gemma4", "cwd": d, "context_limit": 65536})
    finally:
        agent.stream = orig
    assert os.path.exists(target)                 # the worker really did the work
    assert "out.txt" in summary and "Task complete" in summary
    assert "Write" not in summary                 # main context gets the summary, not tool output


def test_worker_requires_prompt():
    out = tools.tool_worker({}, {"model": "gemma4"})
    assert out.startswith("Error") and "self-contained task" in out


def test_worker_summary_is_capped():
    # a runaway worker can't bloat the main context — summary is size-capped
    turns = [AssistantTurn("X" * 20000, [], 5, 5)]
    orig = agent.stream; agent.stream = _fake_subagent(turns)
    try:
        summary = tools.tool_worker({"prompt": "do it"},
                                    {"model": "gemma4", "cwd": "/tmp", "context_limit": 65536})
    finally:
        agent.stream = orig
    assert len(summary) < 20000                   # capped

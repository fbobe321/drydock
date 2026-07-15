"""Tests for dynamic tool selection (PRD Epic F): the 12-tool cap, core tools
always present, phase preference, and task-relevance gating."""
from __future__ import annotations

from drydock.tool_select import CORE_TOOLS, DEFAULT_MAX_TOOLS, select_tools


def _schemas(names):
    return [{"name": n, "description": f"the {n} tool"} for n in names]


ALL = [
    "Read", "Write", "Edit", "Bash", "Glob", "Grep", "todo",
    "GitStatus", "GitDiff", "GitLog", "GitCommit",
    "WebSearch", "WebFetch", "StigRules", "StigRule", "StigSet",
    "GraphQuery", "GraphAdd", "Knowledge", "BuildKnowledge",
    "ViewImage", "Screenshot", "Consult", "task", "Dispatch", "Worker",
]


def _names(schemas):
    return {s["name"] for s in schemas}


def test_under_cap_returns_everything():
    s = _schemas(["Read", "Write", "Bash"])
    assert select_tools(s, max_tools=12) == s


def test_caps_to_max_tools():
    out = select_tools(_schemas(ALL), phase="implement", task_text="fix a bug", max_tools=12)
    assert len(out) == 12


def test_core_tools_always_present():
    # PRD F1.1: a normal coding task keeps the core coding tools.
    out = select_tools(_schemas(ALL), phase="implement",
                       task_text="edit the parser and run tests", max_tools=12)
    names = _names(out)
    for core in CORE_TOOLS:
        assert core in names, f"core tool {core} was trimmed"


def test_unrelated_specialised_tools_dropped_when_over_cap():
    # A plain coding task shouldn't surface STIG/graph tools it never mentioned.
    out = select_tools(_schemas(ALL), phase="implement",
                       task_text="refactor the CSV loader", max_tools=12)
    names = _names(out)
    assert "StigRules" not in names
    assert "GraphAdd" not in names


def test_task_relevance_surfaces_a_family():
    # PRD F1.2-style: a git-focused task keeps git tools over unrelated ones.
    out = select_tools(_schemas(ALL), phase="implement",
                       task_text="commit the fix and show me the git diff", max_tools=12)
    names = _names(out)
    assert "GitDiff" in names or "GitCommit" in names
    assert "StigRules" not in names


def test_web_task_surfaces_web_tools():
    out = select_tools(_schemas(ALL), phase="implement",
                       task_text="fetch the docs from this url and summarize", max_tools=12)
    assert "WebFetch" in _names(out) or "WebSearch" in _names(out)


def test_discover_phase_prefers_read_over_external_mutation():
    # PRD F1.4: while discovering, read/search preferred and external mutation
    # (GitCommit) is not surfaced.
    names = "Read Grep Glob GitStatus GitDiff GitCommit GraphAdd StigSet Worker BuildKnowledge WebSearch Knowledge Consult".split()
    out = select_tools(_schemas(names), phase="discover",
                       task_text="understand how auth works", max_tools=6)
    got = _names(out)
    assert "GitCommit" not in got
    assert "Read" in got and "Grep" in got


def test_default_cap_is_twelve():
    assert DEFAULT_MAX_TOOLS == 12


def test_empty_and_degenerate_inputs():
    assert select_tools([], max_tools=12) == []
    s = _schemas(["Read", "Bash"])
    assert select_tools(s, max_tools=0) == s  # cap<=0 disables trimming

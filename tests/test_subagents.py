"""Tests for sub-agent specs + structured reports (PRD Epic R)."""
from __future__ import annotations

from drydock.subagents import MUTATING_TOOLS, WorkerResult, WorkerSpec


def test_read_only_worker_strips_mutating_tools():
    # PRD R1.2: a read-only worker cannot mutate — even if handed Write/Edit.
    spec = WorkerSpec(objective="inspect the tests",
                      allowed_tools=["Read", "Grep", "Write", "Edit", "Bash"],
                      read_only=True)
    tools = spec.enforced_tools()
    assert "Write" not in tools and "Edit" not in tools
    assert "Read" in tools and "Grep" in tools and "Bash" in tools


def test_writable_worker_keeps_mutating_tools():
    spec = WorkerSpec(objective="implement parse_config",
                      allowed_tools=["Read", "Write", "Edit", "Bash"],
                      read_only=False)
    assert "Write" in spec.enforced_tools()
    assert "Edit" in spec.enforced_tools()


def test_enforced_tools_dedupes_preserving_order():
    spec = WorkerSpec(objective="x", allowed_tools=["Read", "Grep", "Read", "Bash"])
    assert spec.enforced_tools() == ["Read", "Grep", "Bash"]


def test_spec_scopes_objective_not_transcript():
    # PRD R1.1: the worker receives only its scoped objective.
    spec = WorkerSpec(objective="analyze auth tests only", allowed_tools=["Read"])
    d = spec.to_dict()
    assert d["objective"] == "analyze auth tests only"
    assert d["read_only"] is True
    assert "transcript" not in d  # no parent transcript leaks into the spec


def test_worker_result_is_structured():
    # PRD R1.3
    r = WorkerResult(worker_id="w1", summary="added parse_config; tests pass",
                     changed_files=["config.py"], ok=True)
    d = r.to_dict()
    assert d["summary"].startswith("added")
    assert d["changed_files"] == ["config.py"]
    assert d["ok"] is True


def test_mutating_set_covers_the_writers():
    for t in ("Write", "Edit", "GitCommit"):
        assert t in MUTATING_TOOLS

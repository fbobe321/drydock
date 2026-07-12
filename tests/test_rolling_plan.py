"""Rolling plan (Agent-Buildout PRD Epic C): 1 active + <=4 pending steps with
stable ids; revisions bump the version; unchanged steps keep their id."""
from __future__ import annotations

from drydock.task_state import RollingPlan, TaskState, _step_id


def test_stable_ids_survive_revision():
    p = RollingPlan()
    p.update_from_items([("read file", "done"), ("fix bug", "in_progress"), ("test", "pending")])
    ids1 = {s["text"]: s["id"] for s in p.steps}
    # revise: fix bug now done, test now active
    p.update_from_items([("read file", "done"), ("fix bug", "done"), ("test", "in_progress")])
    ids2 = {s["text"]: s["id"] for s in p.steps}
    assert ids1["fix bug"] == ids2["fix bug"]        # same step, same id
    assert ids1["test"] == ids2["test"]


def test_caps_pending_to_four_plus_active():
    p = RollingPlan()
    items = [("active", "in_progress")] + [(f"p{i}", "pending") for i in range(8)]
    p.update_from_items(items)
    pend = [s for s in p.steps if s["status"] == "pending"]
    assert len(pend) == 4 and any(s["status"] == "in_progress" for s in p.steps)


def test_version_bumps_only_on_change():
    p = RollingPlan()
    assert p.update_from_items([("a", "pending")]) and p.version == 1
    assert not p.update_from_items([("a", "pending")]) and p.version == 1   # no change
    assert p.update_from_items([("a", "done")]) and p.version == 2


def test_active_and_remaining():
    p = RollingPlan()
    p.update_from_items([("a", "done"), ("b", "in_progress"), ("c", "pending")])
    assert p.active_step()["text"] == "b" and p.remaining() == 2


def test_serialization_roundtrip():
    ts = TaskState.from_objective("Do it.\n1. a\n2. b")
    ts.plan.update_from_items([("a", "done"), ("b", "in_progress")])
    d = ts.to_dict()
    assert d["plan"]["version"] == 1 and len(d["plan"]["steps"]) == 2
    assert TaskState.from_dict(d).plan.active_step()["text"] == "b"


def test_step_id_deterministic():
    assert _step_id("fix the bug") == _step_id("Fix The Bug ")   # case/space-insensitive

"""Tests for the durable mission state layer (drydock/mission.py)."""
from __future__ import annotations

from drydock import mission as M


def _store(tmp_path):
    return M.MissionStore(tmp_path / "state.db")


def test_create_and_get_mission(tmp_path):
    s = _store(tmp_path)
    mid = s.create_mission("Improve tbench to >=70%",
                           success_criteria={"score": ">=70"},
                           budget={"wall_time_hours": 48, "max_experiments": 100})
    m = s.get_mission(mid)
    assert m["objective"].startswith("Improve tbench")
    assert m["status"] == M.M_CREATED
    assert m["budget"]["max_experiments"] == 100
    assert s.list_missions()[0]["id"] == mid


def test_task_queue_deps_promote_to_ready(tmp_path):
    s = _store(tmp_path)
    mid = s.create_mission("obj")
    a = s.add_task(mid, "task A")                         # no deps → READY
    b = s.add_task(mid, "task B", deps=[a])               # blocked on A → PENDING
    assert s.get_task(a)["status"] == M.T_READY
    assert s.get_task(b)["status"] == M.T_PENDING
    assert [t["id"] for t in s.ready_tasks(mid)] == [a]   # only A ready
    # finish A → B promotes to READY
    s.complete_task(a, M.T_COMPLETED, {"ok": True})
    ready = [t["id"] for t in s.ready_tasks(mid)]
    assert ready == [b]


def test_priority_ordering(tmp_path):
    s = _store(tmp_path)
    mid = s.create_mission("obj")
    s.add_task(mid, "low", priority=1)
    hi = s.add_task(mid, "high", priority=9)
    assert s.ready_tasks(mid)[0]["id"] == hi


def test_lease_is_atomic_and_single_winner(tmp_path):
    s = _store(tmp_path)
    mid = s.create_mission("obj")
    t = s.add_task(mid, "work")
    assert s.claim_task(t, "worker-1") is True
    assert s.claim_task(t, "worker-2") is False          # already leased → no double-claim
    assert s.get_task(t)["assignee"] == "worker-1"
    assert s.get_task(t)["status"] == M.T_RUNNING


def test_expired_lease_is_reclaimed(tmp_path):
    s = _store(tmp_path)
    mid = s.create_mission("obj")
    t = s.add_task(mid, "work")
    s.claim_task(t, "worker-1", lease_secs=-1)            # already expired
    assert s.reclaim_expired(mid) == 1                    # crash recovery (§26/§28)
    assert s.get_task(t)["status"] == M.T_READY and s.get_task(t)["assignee"] is None
    assert s.claim_task(t, "worker-2") is True            # another worker resumes


def test_events_are_appended_and_readable(tmp_path):
    s = _store(tmp_path)
    mid = s.create_mission("obj")
    s.event(mid, "experiment_completed", experiment=27, metric_delta=3.3)
    types = [e["type"] for e in s.events(mid)]
    assert "mission_created" in types and "experiment_completed" in types
    ev = [e for e in s.events(mid) if e["type"] == "experiment_completed"][0]
    assert ev["metric_delta"] == 3.3 and "seq" in ev and "ts" in ev


def test_metric_tracks_best(tmp_path):
    s = _store(tmp_path)
    mid = s.create_mission("obj")
    s.set_metric(mid, 61.4)
    s.set_metric(mid, 66.9)
    s.set_metric(mid, 64.2)                               # a regression
    m = s.get_mission(mid)
    assert m["current_metric"] == 64.2 and m["best_metric"] == 66.9


def test_budget_exhaustion(tmp_path):
    s = _store(tmp_path)
    mid = s.create_mission("obj", budget={"max_experiments": 3, "max_failures": 2})
    assert s.budget_exhausted(mid) == (False, "")
    s.add_usage(mid, experiments=3)
    done, why = s.budget_exhausted(mid)
    assert done and "experiment budget" in why
    # failures also count, via complete_task(FAILED)
    mid2 = s.create_mission("o2", budget={"max_failures": 1})
    t = s.add_task(mid2, "w")
    s.complete_task(t, M.T_FAILED)
    assert s.budget_exhausted(mid2)[0] is True


def test_state_persists_across_reopen(tmp_path):
    mid, store = M.create_mission(tmp_path, "durable obj",
                                  budget={"wall_time_hours": 6})
    store.add_task(mid, "t1")
    store.set_status(mid, M.M_EXECUTING)
    store.close()
    # a fresh process/handle sees everything (survives Drydock restart, §28/AT-2)
    again = M.open_store(tmp_path, mid)
    m = again.get_mission(mid)
    assert m["status"] == M.M_EXECUTING and m["objective"] == "durable obj"
    assert len(again.tasks(mid)) == 1
    assert mid in M.list_missions(tmp_path)

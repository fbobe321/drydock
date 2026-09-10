"""Tests for the mission controller loop (drydock/mission_run.py) — no model needed."""
from __future__ import annotations

import subprocess
from pathlib import Path

from drydock import mission as M
from drydock import mission_run as R


def _repo(tmp_path: Path) -> str:
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "README.md").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=r, check=True)
    return str(r)


def _writer_worker(fname: str, content: str = "y"):
    def w(task, mission, cwd, base_config):
        (Path(cwd) / fname).write_text(content)
        return R.WorkerResult(ok=True, summary=f"wrote {fname}", in_tokens=10, out_tokens=5)
    return w


def test_success_met_numeric():
    assert R.success_met({"score": ">=70"}, 72.0) is True
    assert R.success_met({"score": ">=70"}, 61.0) is False
    assert R.success_met({"score": ">=70", "x": "<5"}, 72.0) is False  # all numeric must hold (72<5 fails)
    assert R.success_met({}, 100.0) is False        # no criterion → not success
    assert R.success_met({"tests": "pass"}, 100.0) is False  # non-numeric only → not met in MVP


def test_run_task_keep_commits_and_updates_metric(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj")
    tid = s.add_task(mid, "make fix")
    m = s.get_mission(mid)
    out = R.run_task(s, s.get_task(tid), mission=m, cwd=repo, repo=repo,
                     worker=_writer_worker("fix.py"),
                     evaluator=lambda *a: R.Evaluation(accept=True, metric_before=0, metric_after=66.9))
    assert out.accept and not out.reverted
    assert s.get_task(tid)["status"] == M.T_COMPLETED
    assert s.get_mission(mid)["current_metric"] == 66.9
    assert (Path(repo) / "fix.py").exists()          # kept


def test_run_task_revert_restores_repo(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj")
    s.set_metric(mid, 60.0)
    tid = s.add_task(mid, "risky change")
    m = s.get_mission(mid)
    out = R.run_task(s, s.get_task(tid), mission=m, cwd=repo, repo=repo,
                     worker=_writer_worker("bad.py"),
                     evaluator=lambda *a: R.Evaluation(accept=False, metric_before=60, metric_after=57,
                                                       reason="regressed"))
    assert not out.accept and out.reverted
    assert not (Path(repo) / "bad.py").exists()      # auto-reverted (§19)
    # the negative result is still in history even though the code is gone (§16)
    exps = [e for e in s.events(mid) if e["type"] == "experiment"]
    assert exps and exps[-1]["decision"] == "REVERT"


def test_run_mission_stops_on_success(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj", success_criteria={"score": ">=50"})
    s.add_task(mid, "t1")
    summ = R.run_mission(s, mid, cwd=repo, repo=repo, worker=_writer_worker("a.py"),
                         evaluator=lambda *a: R.Evaluation(accept=True, metric_after=60.0))
    assert summ.status == M.M_COMPLETED and summ.final_metric == 60.0


def test_run_mission_blocks_on_stagnation(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj", success_criteria={"score": ">=999"})
    for i in range(3):
        s.add_task(mid, f"t{i}")
    summ = R.run_mission(s, mid, cwd=repo, repo=repo, worker=_writer_worker("x.py"),
                         evaluator=lambda *a: R.Evaluation(accept=False, metric_after=0.0),
                         stagnation_limit=3)
    assert summ.status == M.M_BLOCKED


def test_run_mission_stops_on_budget(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj", success_criteria={"score": ">=999"},
                           budget={"max_experiments": 1})
    for i in range(3):
        s.add_task(mid, f"t{i}")
    summ = R.run_mission(s, mid, cwd=repo, repo=repo, worker=_writer_worker("x.py"),
                         evaluator=lambda *a: R.Evaluation(accept=False, metric_after=0.0))
    assert summ.status == M.M_BUDGET_EXHAUSTED


def test_establish_baseline_is_immutable(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj")
    b1 = R.establish_baseline(s, mid, repo, lambda *a: R.Evaluation(accept=True, metric_after=61.4))
    assert b1 == 61.4
    assert s.get_mission(mid)["baseline"]["metric"] == 61.4
    assert s.get_mission(mid)["current_metric"] == 61.4
    # second call is a no-op — the baseline is immutable mission history (§9)
    assert R.establish_baseline(s, mid, repo, lambda *a: R.Evaluation(accept=True, metric_after=99.0)) is None
    assert s.get_mission(mid)["baseline"]["metric"] == 61.4


def test_verifier_evaluator_measures(tmp_path):
    repo = _repo(tmp_path)
    (Path(repo) / "answer.txt").write_text("PASS")
    ev = R.make_verifier_evaluator("grep -q PASS answer.txt", fitness="exitcode")
    r = ev({}, {}, repo, 0.0)
    assert r.accept and r.metric_after == 100.0 and r.passed

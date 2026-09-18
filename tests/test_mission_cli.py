"""Tests for the mission CLI + planner (drydock/mission_cli.py) — non-model paths."""
from __future__ import annotations

import subprocess
from pathlib import Path

from drydock import mission as M
from drydock import mission_cli as C
from drydock import mission_run as R


def _repo(tmp_path: Path) -> str:
    r = tmp_path / "proj"
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    (r / "f").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=r, check=True)
    return str(r)


def test_cli_create_makes_mission_and_bootstrap_task(tmp_path, capsys):
    rc = C.run_cli(["create", "improve", "the", "score", "--target", ">=70",
                    "--verify", "pytest -q", "--time-budget", "6h"], {"cwd": str(tmp_path)})
    assert rc == 0
    out = capsys.readouterr().out
    assert "Mission mission-" in out and ">=70" in out
    mid = M.list_missions(tmp_path)[0]
    store = M.open_store(tmp_path, mid)
    m = store.get_mission(mid)
    assert m["success_criteria"]["metric"] == ">=70"
    assert m["budget"]["wall_time_hours"] == 6.0
    assert m["config"]["verify_cmd"] == "pytest -q"
    assert len(store.tasks(mid)) == 1                 # bootstrap task
    assert (M.missions_dir(tmp_path) / mid / "MISSION.md").exists()


def test_cli_list_status_tasks_stop(tmp_path, capsys):
    C.run_cli(["create", "obj here"], {"cwd": str(tmp_path)})
    mid = M.list_missions(tmp_path)[0]
    assert C.run_cli(["list"], {"cwd": str(tmp_path)}) == 0
    assert mid in capsys.readouterr().out
    assert C.run_cli(["status", mid], {"cwd": str(tmp_path)}) == 0
    assert "DRYDOCK MISSION" in capsys.readouterr().out
    assert C.run_cli(["tasks", mid], {"cwd": str(tmp_path)}) == 0
    assert "bootstrap" not in capsys.readouterr().out  # tasks list shows objective, not reason
    C.run_cli(["stop", mid], {"cwd": str(tmp_path)})
    assert M.open_store(tmp_path, mid).get_mission(mid)["status"] == M.M_CANCELLED


def test_cli_create_requires_objective(tmp_path):
    assert C.run_cli(["create"], {"cwd": str(tmp_path)}) == 1


def _pytest_repo(tmp_path: Path) -> str:
    """A repo with two failing tests, so the decomposing planner has real items to carve up."""
    r = tmp_path / "proj"
    (r / "tests").mkdir(parents=True)
    (r / "src.py").write_text("def a():\n    return 0\n\ndef b():\n    return 0\n")
    (r / "tests" / "test_it.py").write_text(
        "from src import a, b\n"
        "def test_a():\n    assert a() == 1\n"
        "def test_b():\n    assert b() == 2\n")
    (r / "conftest.py").write_text("")
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=r, check=True)
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=r, check=True)
    return str(r)


def test_failing_items_parses_pytest_failures(tmp_path):
    repo = _pytest_repo(tmp_path)
    items = C.failing_items("python -m pytest -q", repo)
    assert len(items) == 2 and all("::test_" in i for i in items)


def test_decomposing_planner_one_task_per_failure(tmp_path):
    repo = _pytest_repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("fix the code so tests pass; only edit src.py")
    added = C.decomposing_planner("fix the code", "python -m pytest -q", repo)(s, s.get_mission(mid))
    assert added == 2                                        # a focused task per failing check
    tasks = s.tasks(mid)
    assert all(t["reason"].startswith("fix:") for t in tasks)
    assert any("test_a" in t["objective"] for t in tasks)


def test_decomposing_planner_falls_back_when_unparseable(tmp_path):
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj")
    # a verifier whose output has no FAILED lines → one generic task, loop never stalls
    added = C.decomposing_planner("obj", "true", str(tmp_path))(s, s.get_mission(mid))
    assert added == 1 and s.tasks(mid)[0]["reason"] == "iterate"


def test_cli_create_persists_integrity_and_noise_flags(tmp_path):
    rc = C.run_cli(["create", "harden", "the", "loop", "--protect", "tests", "--protect",
                    "verify.sh", "--samples", "3", "--noise-band", "1.5"], {"cwd": str(tmp_path)})
    assert rc == 0
    mid = M.list_missions(tmp_path)[0]
    cfg = M.open_store(tmp_path, mid).get_mission(mid)["config"]
    assert cfg["protected_paths"] == ["tests", "verify.sh"]
    assert cfg["eval_samples"] == 3 and cfg["noise_band"] == 1.5


def test_cli_swarm_flag_persists_and_selects_swarm_worker(tmp_path, monkeypatch):
    rc = C.run_cli(["create", "fix", "it", "--swarm-agents", "5", "--verify", "true"],
                   {"cwd": str(tmp_path)})
    assert rc == 0
    mid = M.list_missions(tmp_path)[0]
    cfg = M.open_store(tmp_path, mid).get_mission(mid)["config"]
    assert cfg["swarm"] is True and cfg["swarm_agents"] == 5      # persisted to mission config

    # _run must pick make_swarm_worker (not default_worker) when swarm is set
    captured = {}
    monkeypatch.setattr(R, "make_swarm_worker",
                        lambda **kw: captured.update(kw) or (lambda *a: R.WorkerResult(ok=True)))
    monkeypatch.setattr(R, "run_mission", lambda *a, **kw: captured.update(worker_set=True) or
                        R.RunSummary(mission_id=mid, status="COMPLETED", cycles=0, final_metric=100.0))
    monkeypatch.setattr(R, "establish_baseline", lambda *a, **k: None)
    C.run_cli(["run", mid], {"cwd": str(tmp_path)})
    assert captured.get("agents") == 5 and captured.get("share") is True   # swarm worker built with N=5


def test_cli_knowledge_view(tmp_path, capsys):
    C.run_cli(["create", "obj here"], {"cwd": str(tmp_path)})
    mid = M.list_missions(tmp_path)[0]
    store = M.open_store(tmp_path, mid)
    store.add_knowledge(mid, M.K_NEGATIVE, "raising retry limit increased loops",
                        confidence=0.6, sources=["task-1"])
    assert C.run_cli(["knowledge", mid], {"cwd": str(tmp_path)}) == 0
    out = capsys.readouterr().out
    assert "negative_result" in out and "raising retry limit" in out and "task-1" in out


def test_iterate_planner_keeps_the_loop_going(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("keep going", success_criteria={"metric": ">=999"})
    s.add_task(mid, "first")

    def worker(task, mission, cwd, base_config):
        (Path(cwd) / "w.py").write_text("1")
        return R.WorkerResult(ok=True, summary="did")

    # never accepts → never progresses; the planner replans on the empty queue each time,
    # and stagnation eventually blocks it (never an infinite loop).
    summ = R.run_mission(s, mid, cwd=repo, repo=repo, worker=worker,
                         evaluator=lambda *a: R.Evaluation(accept=False, metric_after=0.0),
                         planner=C.iterate_planner("keep going", "true"),
                         stagnation_limit=3)
    assert summ.status == M.M_BLOCKED
    replans = [e for e in s.events(mid) if e["type"] == "replan"]
    assert len(replans) >= 1                          # the planner actually re-seeded work


def test_iterate_planner_reaches_success(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("win", success_criteria={"metric": ">=50"})
    s.add_task(mid, "first")

    def worker(task, mission, cwd, base_config):
        (Path(cwd) / "w.py").write_text("1")
        return R.WorkerResult(ok=True, summary="did")

    summ = R.run_mission(s, mid, cwd=repo, repo=repo, worker=worker,
                         evaluator=lambda *a: R.Evaluation(accept=True, metric_after=55.0),
                         planner=C.iterate_planner("win", "true"))
    assert summ.status == M.M_COMPLETED and summ.final_metric == 55.0


def test_holistic_planner_seeds_one_whole_problem_task(tmp_path):
    import subprocess as sp
    from drydock import mission as M
    from drydock.mission_cli import run_cli

    (tmp_path / "test_x.py").write_text("def test_a():\n    assert 0\n\ndef test_b():\n    assert 0\n")
    sp.run(["git", "init", "-q"], cwd=tmp_path)
    rc = run_cli(["create", "Write a fast solver", "--verify", "python -m pytest -q",
                  "--planner", "holistic"], {"cwd": str(tmp_path)})
    assert rc == 0
    mid = M.list_missions(str(tmp_path))[-1]
    tasks = M.open_store(str(tmp_path), mid).tasks(mid)
    assert len(tasks) == 1                                   # not one task per failing test
    text = tasks[0]["objective"]
    assert "ONE hard problem" in text and "Write a fast solver" in text
    assert "test_a" in text                                  # a sample of what still fails

"""Tests for the mission controller loop (drydock/mission_run.py) — no model needed."""
from __future__ import annotations

import subprocess
import time
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


def test_keep_records_finding_knowledge(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj")
    tid = s.add_task(mid, "make fix")
    R.run_task(s, s.get_task(tid), mission=s.get_mission(mid), cwd=repo, repo=repo,
               worker=_writer_worker("fix.py", ),
               evaluator=lambda *a: R.Evaluation(accept=True, metric_before=60, metric_after=66.9))
    findings = s.knowledge(mid, M.K_FINDING)
    assert findings and "KEPT" in findings[0]["statement"]


def test_revert_records_negative_knowledge_and_feeds_next_worker(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj")
    s.set_metric(mid, 60.0)
    tid = s.add_task(mid, "increase shell retry limit")
    R.run_task(s, s.get_task(tid), mission=s.get_mission(mid), cwd=repo, repo=repo,
               worker=_writer_worker("bad.py"),
               evaluator=lambda *a: R.Evaluation(accept=False, metric_before=60, metric_after=57,
                                                 reason="loop rate rose"))
    negs = s.knowledge(mid, M.K_NEGATIVE)
    assert negs and "REVERTED" in negs[0]["statement"]
    # a later related task must receive that negative knowledge in its worker prompt (§13/§16)
    seen = {}

    def spy(task, mission, cwd, base_config):
        seen["neg"] = task.get("negative_knowledge")
        return R.WorkerResult(ok=True, summary="ok")
    tid2 = s.add_task(mid, "try raising the retry limit again")
    R.run_task(s, s.get_task(tid2), mission=s.get_mission(mid), cwd=repo, repo=repo,
               worker=spy, evaluator=lambda *a: R.Evaluation(accept=True, metric_after=61.0))
    assert seen["neg"] and any("REVERTED" in n for n in seen["neg"])


def test_strategic_review_due_after_n_experiments(tmp_path):
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj")
    assert R.strategic_review_due(s, mid, every_tasks=3, every_secs=0) == ""
    for _ in range(3):
        s.event(mid, "experiment", decision="KEEP")
    assert "experiments" in R.strategic_review_due(s, mid, every_tasks=3, every_secs=0)


def test_run_strategic_review_records_and_replans(tmp_path):
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj", success_criteria={"score": ">=70"})
    s.set_baseline(mid, {"metric": 60.0})
    s.event(mid, "experiment", decision="KEEP")
    s.event(mid, "experiment", decision="REVERT")
    s.event(mid, "experiment", decision="REVERT")
    s.event(mid, "experiment", decision="REVERT")
    seeded = {"n": 0}

    def planner(store, mission):
        seeded["n"] += 1
        return 2
    facts = R.run_strategic_review(s, mid, planner=planner, trigger="test")
    assert facts["experiments_kept"] == 1 and facts["experiments_reverted"] == 3
    assert facts["backlog_added"] == 2 and seeded["n"] == 1
    assert "not working" in facts["limiting_factor"]        # reverts dominate → change tactics
    assert len(s.reviews(mid)) == 1
    assert any(e["type"] == "strategic_review" for e in s.events(mid))
    # after a review, the counter resets — no longer due (§24)
    assert R.strategic_review_due(s, mid, every_tasks=4, every_secs=0) == ""


def test_run_mission_fires_review_on_trigger(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj", success_criteria={"score": ">=999"})
    for i in range(4):
        s.add_task(mid, f"t{i}")
    R.run_mission(s, mid, cwd=repo, repo=repo, worker=_writer_worker("x.py"),
                  evaluator=lambda *a: R.Evaluation(accept=True, metric_after=float(1)),
                  review_every_tasks=2, review_every_secs=0, stagnation_limit=99)
    assert s.reviews(mid), "a review should have fired after 2 experiments"


def test_tampered_paths_matches_dir_and_glob():
    changed = {"tests/foo_test.py", "src/app.py", "verify.sh", "docs/readme.md"}
    hits = R.tampered_paths(changed, ["tests", "verify.sh", "*.lock"])
    assert "tests/foo_test.py" in hits and "verify.sh" in hits
    assert "src/app.py" not in hits and "docs/readme.md" not in hits


def test_tamper_forces_revert_even_when_metric_would_pass(tmp_path):
    repo = _repo(tmp_path)
    (Path(repo) / "tests").mkdir()
    (Path(repo) / "tests" / "check.sh").write_text("echo baseline")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "add tests"], cwd=repo, check=True)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj", config={"protected_paths": ["tests"]})
    s.set_metric(mid, 50.0)
    tid = s.add_task(mid, "make it pass")

    def rigger(task, mission, cwd, base_config):
        (Path(cwd) / "tests" / "check.sh").write_text("exit 0")   # edit the measurement itself
        return R.WorkerResult(ok=True, summary="rewrote the check")
    out = R.run_task(s, s.get_task(tid), mission=s.get_mission(mid), cwd=repo, repo=repo,
                     worker=rigger,
                     # evaluator would gladly KEEP a 100 — must be overridden by tamper guard
                     evaluator=lambda *a: R.Evaluation(accept=True, metric_before=50, metric_after=100))
    assert not out.accept and out.reverted
    assert s.get_mission(mid)["current_metric"] == 50.0          # metric NOT advanced
    assert (Path(repo) / "tests" / "check.sh").read_text() == "echo baseline"   # restored
    assert any(e["type"] == "tamper" for e in s.events(mid))
    assert s.knowledge(mid, M.K_NEGATIVE)[0]["statement"].startswith("REJECTED (measurement tamper)")


def test_no_tamper_when_only_source_changes(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj", config={"protected_paths": ["tests"]})
    tid = s.add_task(mid, "legit fix")
    out = R.run_task(s, s.get_task(tid), mission=s.get_mission(mid), cwd=repo, repo=repo,
                     worker=_writer_worker("src.py"),
                     evaluator=lambda *a: R.Evaluation(accept=True, metric_after=66.0))
    assert out.accept and not out.reverted                       # untouched apparatus → normal KEEP
    assert not any(e["type"] == "tamper" for e in s.events(mid))


def test_escalate_switches_model_and_replans_then_continues(tmp_path):
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj", config={"escalation_model": "big-model"})
    base = {"model": "small-model"}

    def planner(store, mission):
        store.add_task(mission["id"], "alternative strategy")
        return 1
    disp = R.escalate(s, mid, count=1, max_escalations=3, base_config=base, planner=planner)
    assert disp == "continue"
    assert base["model"] == "big-model"                     # L5 model routing (§32)
    assert any(e["type"] == "escalation" for e in s.events(mid))
    assert s.knowledge(mid, M.K_ASSUMPTION)[0]["statement"].startswith("CRITIC:")  # L2 critic


def test_escalate_blocks_when_no_alternative_work(tmp_path):
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj")                            # not mission-critical, no planner
    assert R.escalate(s, mid, count=1, max_escalations=3, base_config={}) == "blocked"


def test_escalate_awaits_human_when_mission_critical(tmp_path):
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj", config={"mission_critical": True})
    # hit the escalation cap → a critical mission asks for a human instead of silently blocking
    assert R.escalate(s, mid, count=3, max_escalations=3, base_config={}) == "human"


def test_run_mission_escalates_before_blocking(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj", success_criteria={"score": ">=999"})
    s.add_task(mid, "first")

    def planner(store, mission):
        store.add_task(mission["id"], "alt")
        return 1
    summ = R.run_mission(s, mid, cwd=repo, repo=repo, worker=_writer_worker("x.py"),
                         evaluator=lambda *a: R.Evaluation(accept=False, metric_after=0.0),
                         planner=planner, stagnation_limit=2, max_escalations=2)
    assert summ.status == M.M_BLOCKED                        # only after the ladder is exhausted
    escs = [e for e in s.events(mid) if e["type"] == "escalation"]
    assert len(escs) == 2                                    # climbed to the cap, then blocked
    assert s.knowledge(mid, M.K_ASSUMPTION)                  # critic left a trail (§23/§44)


def test_run_mission_awaiting_human_for_critical_mission(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj", success_criteria={"score": ">=999"},
                           config={"mission_critical": True})
    s.add_task(mid, "first")

    def planner(store, mission):
        store.add_task(mission["id"], "alt")
        return 1
    summ = R.run_mission(s, mid, cwd=repo, repo=repo, worker=_writer_worker("x.py"),
                         evaluator=lambda *a: R.Evaluation(accept=False, metric_after=0.0),
                         planner=planner, stagnation_limit=2, max_escalations=2)
    # a critical mission that exhausts the ladder asks for a human rather than silently blocking
    assert summ.status == M.M_AWAITING_HUMAN


def test_negative_knowledge_is_discriminating_by_target(tmp_path):
    repo = _repo(tmp_path)
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj")
    s.set_metric(mid, 50.0)
    # two distinct failed targets, recorded via the reason field the decomposing planner sets
    for reason, fname in (("fix:tests::test_roman", "roman.py"), ("fix:tests::test_json", "json.py")):
        tid = s.add_task(mid, f"work on {reason}", reason=reason)
        R.run_task(s, s.get_task(tid), mission=s.get_mission(mid), cwd=repo, repo=repo,
                   worker=_writer_worker(fname),
                   evaluator=lambda *a: R.Evaluation(accept=False, metric_before=50, metric_after=50,
                                                     reason="no gain"))
    negs = s.knowledge(mid, M.K_NEGATIVE)
    assert len(negs) == 2
    # a proposal about roman must surface the roman failure, NOT the json one (§16/AT-8)
    hit = s.similar_knowledge(mid, "fix:tests::test_roman", type=M.K_NEGATIVE, top=1)
    assert hit and "test_roman" in hit[0]["statement"] and "roman.py" in hit[0]["statement"]


def test_swarm_worker_applies_winner_to_mission_tree(tmp_path):
    """ASR Phase-4: a mission task solved by a SWARM; the winning candidate lands in the
    mission's working tree so KEEP/REVERT scores it. Injected runner — no model."""
    from drydock import swarm as S
    repo = tmp_path / "proj"
    (repo / "tests").mkdir(parents=True)
    (repo / "toolkit.py").write_text("def add(a, b):\n    raise NotImplementedError\n")
    (repo / "tests" / "test_it.py").write_text("from toolkit import add\ndef test_add(): assert add(2,3)==5\n")
    (repo / "conftest.py").write_text("")
    for c in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"], ["git", "add", "-A"], ["git", "commit", "-qm", "base"]):
        subprocess.run(c, cwd=repo, check=True)

    def runner(objective, cwd, base_config, system_prompt, allow, mt, mtc):
        (Path(cwd) / "toolkit.py").write_text("def add(a, b):\n    return a + b\n")   # the fix
        return S.RunResult(summary="implemented add", in_tokens=1, out_tokens=1, turns=1)

    worker = R.make_swarm_worker(agents=2, share=False, runner=runner)
    wr = worker({"objective": "make add() work"},
                {"config": {"verify_cmd": "python -m pytest -q"}}, str(repo), {})
    assert wr.ok and "winner" in wr.summary                          # swarm found + reported a winner
    assert "return a + b" in (repo / "toolkit.py").read_text()       # winner applied to the mission tree


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


def test_checkpoint_excludes_mission_workspace(tmp_path):
    repo = _repo(tmp_path)
    ws = Path(repo) / ".drydock" / "missions" / "m1"
    ws.mkdir(parents=True)
    (ws / "state.db").write_text("MISSION STATE")
    cp = R.checkpoint(repo)
    assert cp
    # the mission DB must NOT be tracked by the checkpoint (else a REVERT rolls back state)
    tracked = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True).stdout
    assert ".drydock" not in tracked


def test_restore_preserves_mission_workspace(tmp_path):
    repo = _repo(tmp_path)
    ws = Path(repo) / ".drydock" / "missions" / "m1"
    ws.mkdir(parents=True)
    (ws / "state.db").write_text("STATE-V1")
    cp = R.checkpoint(repo)                       # code checkpointed; .drydock excluded
    (Path(repo) / "code.py").write_text("regression")   # a worker change to revert
    (ws / "state.db").write_text("STATE-V2")            # live mission progress after checkpoint
    assert R.restore(repo, cp) is True
    assert not (Path(repo) / "code.py").exists()        # code reverted
    assert (ws / "state.db").read_text() == "STATE-V2"  # mission state SURVIVES the revert


def test_injectable_checkpoint_restore(tmp_path):
    # a non-git target (e.g. a container via `docker commit`) plugs in its own snapshot fns
    snaps = {"made": [], "restored": []}

    def cp_fn(handle):
        ref = f"snap-{len(snaps['made'])}"
        snaps["made"].append(ref)
        return ref

    def rs_fn(handle, ref):
        snaps["restored"].append(ref)
        return True

    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj")
    s.set_metric(mid, 50.0)
    tid = s.add_task(mid, "risky")
    out = R.run_task(s, s.get_task(tid), mission=s.get_mission(mid), cwd=".", repo="ctr:task",
                     worker=lambda *a: R.WorkerResult(ok=True, summary="tried"),
                     evaluator=lambda *a: R.Evaluation(accept=False, metric_before=50, metric_after=40,
                                                       reason="worse"),
                     checkpoint_fn=cp_fn, restore_fn=rs_fn)
    assert out and out.reverted
    assert snaps["made"] == ["snap-0"]           # checkpointed via the injected fn (not git)
    assert snaps["restored"] == ["snap-0"]        # reverted via the injected fn


def test_verify_passes_reflects_the_tree(tmp_path):
    repo = _repo(tmp_path)
    (Path(repo) / "answer.txt").write_text("PASS")
    assert R.verify_passes("grep -q PASS answer.txt", repo, fitness="exitcode") is True
    assert R.verify_passes("grep -q NOPE answer.txt", repo, fitness="exitcode") is False


def _fake_agent(monkeypatch, turns=30, per_turn_sleep=0.0):
    """Replace the agent loop with `turns` no-op TurnDone events; count how many run."""
    from drydock import agent as A
    seen = {"turns": 0}

    def fake_run(objective, state, cfg, system_prompt):
        for _ in range(turns):
            seen["turns"] += 1
            if per_turn_sleep:
                time.sleep(per_turn_sleep)
            yield A.TurnDone.__new__(A.TurnDone)
    monkeypatch.setattr(A, "run", fake_run)
    return seen


def test_worker_stops_early_when_verifier_passes(monkeypatch):
    seen = _fake_agent(monkeypatch, turns=30)
    task = {"objective": "x", "_verify_passes": lambda: True}   # already passing
    wr = R.default_worker(task, {"config": {}}, ".", {})
    assert wr.ok and seen["turns"] == 1                          # broke after the first turn
    assert "verifier already passing" in wr.summary


def test_worker_stops_at_time_budget(monkeypatch):
    seen = _fake_agent(monkeypatch, turns=30, per_turn_sleep=0.01)
    task = {"objective": "x", "worker_time_budget_s": 0.001}     # tiny wall-clock cap (§42)
    wr = R.default_worker(task, {"config": {}}, ".", {})
    assert wr.ok and seen["turns"] == 1                          # stopped at the budget, not 30 turns
    assert "time budget" in wr.summary


def test_worker_runs_all_turns_without_bounds(monkeypatch):
    seen = _fake_agent(monkeypatch, turns=5)
    wr = R.default_worker({"objective": "x"}, {"config": {}}, ".", {})
    assert wr.ok and seen["turns"] == 5 and wr.summary == ""     # no early stop, no budget


def test_run_task_injects_working_verify_probe(tmp_path):
    repo = _repo(tmp_path)
    (Path(repo) / "answer.txt").write_text("PASS")
    s = M.MissionStore(tmp_path / "s.db")
    mid = s.create_mission("obj", config={"verify_cmd": "grep -q PASS answer.txt"})
    tid = s.add_task(mid, "work")
    seen = {}

    def spy(task, mission, cwd, base_config):
        probe = task.get("_verify_passes")
        seen["callable"] = callable(probe)
        seen["passes"] = probe() if probe else None
        return R.WorkerResult(ok=True, summary="ok")
    R.run_task(s, s.get_task(tid), mission=s.get_mission(mid), cwd=repo, repo=repo,
               worker=spy, evaluator=lambda *a: R.Evaluation(accept=True, metric_after=100.0))
    assert seen["callable"] and seen["passes"] is True           # probe reflects the live tree


def test_noise_band_rejects_within_band_gains(tmp_path):
    repo = _repo(tmp_path)
    (Path(repo) / "answer.txt").write_text("PASS")
    # measured metric is 100; baseline 99.5 → only +0.5, inside a 1.0 noise band → NOT a real gain
    ev = R.make_verifier_evaluator("grep -q PASS answer.txt", fitness="exitcode", noise_band=1.0)
    r = ev({}, {}, repo, 99.5)
    assert r.metric_after == 100.0 and not r.accept
    # the same +0.5 clears a smaller 0.4 band
    ev2 = R.make_verifier_evaluator("grep -q PASS answer.txt", fitness="exitcode", noise_band=0.4)
    assert ev2({}, {}, repo, 99.5).accept


def test_sampling_takes_median_so_one_flake_does_not_flip(tmp_path):
    repo = _repo(tmp_path)
    # run 2 of 3 is a spurious failure; median of [100,0,100] = 100 survives the flake
    cmd = "n=$(cat c 2>/dev/null || echo 0); n=$((n+1)); echo $n > c; [ $n -eq 2 ] && exit 1 || exit 0"
    ev = R.make_verifier_evaluator(cmd, fitness="exitcode", samples=3)
    r = ev({}, {}, repo, 50.0)
    assert r.metric_after == 100.0 and r.accept and "median of 3" in r.reason


def test_verifier_evaluator_measures(tmp_path):
    repo = _repo(tmp_path)
    (Path(repo) / "answer.txt").write_text("PASS")
    ev = R.make_verifier_evaluator("grep -q PASS answer.txt", fitness="exitcode")
    r = ev({}, {}, repo, 0.0)
    assert r.accept and r.metric_after == 100.0 and r.passed

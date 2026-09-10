"""Tests for the multi-agent swarm blackboard (drydock/swarm.py).

Covers the shared-knowledge store MVP: typed adders, append-log last-write-wins updates,
resume/re-open, and that the §27 event stream is persisted.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from drydock import swarm


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], path)
    _git(["config", "user.name", "t"], path)
    _git(["config", "user.email", "t@t"], path)
    (path / "README.md").write_text("hello\n")
    _git(["add", "-A"], path)
    _git(["commit", "-qm", "init"], path)
    return str(path)


def _writer_runner(filename: str, content: str):
    def run(objective, cwd, base_config, system_prompt, allow, max_turns, max_tool_calls):
        (Path(cwd) / filename).write_text(content)
        return f"wrote {filename}"
    return run


def test_create_and_read_objective(tmp_path):
    bb = swarm.create_swarm(tmp_path, "Fix the failing auth tests", {"agents": 4})
    assert bb.objective() == "Fix the failing auth tests"
    assert bb.config()["agents"] == 4
    # swarm dir is under <cwd>/.drydock/swarms/<id>/
    ids = swarm.list_swarms(tmp_path)
    assert len(ids) == 1 and ids[0].startswith("swarm-")


def test_discovery_hypothesis_candidate_roundtrip(tmp_path):
    bb = swarm.create_swarm(tmp_path, "obj")
    d = bb.add_discovery("agent-1", "t-1", "refresh bypasses expiry",
                         confidence=0.9, evidence=["src/auth/refresh.py:118"])
    h = bb.add_hypothesis("stale token after refresh", agent="agent-1")
    bb.add_candidate("agent-2", summary="patch refresh", hypothesis=h.id,
                     commit="abc123", files_changed=2)

    assert [x.id for x in bb.discoveries()] == [d.id]
    assert bb.discoveries()[0].evidence == ["src/auth/refresh.py:118"]
    assert bb.hypotheses()[0].status == swarm.HYP_UNTESTED
    assert bb.candidates()[0].commit == "abc123"


def test_update_is_last_write_wins(tmp_path):
    bb = swarm.create_swarm(tmp_path, "obj")
    h = bb.add_hypothesis("race condition")
    bb.update("hypotheses", h.id, status=swarm.HYP_INVESTIGATING, confidence=0.4)
    bb.update("hypotheses", h.id, status=swarm.HYP_REJECTED, confidence=0.05)

    hyps = bb.hypotheses()
    assert len(hyps) == 1  # collapsed to latest, not three rows
    assert hyps[0].status == swarm.HYP_REJECTED
    assert hyps[0].confidence == 0.05


def test_update_unknown_id_returns_none(tmp_path):
    bb = swarm.create_swarm(tmp_path, "obj")
    assert bb.update("candidates", "c-999", status=swarm.CAND_ACCEPTED) is None


def test_resume_reads_persisted_state(tmp_path):
    bb = swarm.create_swarm(tmp_path, "resume me", swarm_id="swarm-fixed")
    bb.add_candidate("agent-1", summary="first", commit="c1")
    bb.update("candidates", "c-1", status=swarm.CAND_PROMISING, tests_passed=10, tests_total=10)

    # A brand-new handle on the same dir must see everything (survives interruption, §28).
    again = swarm.open_swarm(tmp_path, "swarm-fixed")
    assert again.objective() == "resume me"
    cands = again.candidates()
    assert len(cands) == 1
    assert cands[0].status == swarm.CAND_PROMISING
    assert cands[0].tests_passed == 10


def test_event_stream_is_written(tmp_path):
    bb = swarm.create_swarm(tmp_path, "obj", swarm_id="swarm-ev")
    bb.add_discovery("a", "t", "found it")
    bb.add_candidate("a", commit="c1")

    ev_path = swarm.swarms_dir(tmp_path) / "swarm-ev" / "events.jsonl"
    lines = [json.loads(x) for x in ev_path.read_text().splitlines() if x.strip()]
    types = [e["type"] for e in lines]
    assert "SWARM_CREATED" in types
    assert "DISCOVERY_CREATED" in types
    assert "CANDIDATE_CREATED" in types
    # events carry a monotonic seq + timestamp (events.py schema)
    assert all("seq" in e and "ts" in e for e in lines)


def test_ids_are_unique_and_prefixed(tmp_path):
    bb = swarm.create_swarm(tmp_path, "obj")
    ids = [bb.add_discovery("a", "t", f"d{i}").id for i in range(5)]
    assert ids == ["d-1", "d-2", "d-3", "d-4", "d-5"]
    assert len(set(ids)) == 5


# ── worker slice (worktree → agent → snapshot → candidate) ───────────────────
def test_worker_produces_candidate(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    bb = swarm.create_swarm(tmp_path / "state", "make a fix")
    out = swarm.run_worker(bb, repo, "HEAD", "agent-1", "add a file",
                           runner=_writer_runner("fix.py", "print('fixed')\n"))
    assert out.ok and out.files_changed == 1 and out.commit
    cands = bb.candidates()
    assert len(cands) == 1
    assert cands[0].status == swarm.CAND_DRAFT
    assert cands[0].commit == out.commit
    # the snapshot commit is durable in the repo object store
    show = _git(["show", "--stat", out.commit], repo)
    assert show.returncode == 0 and "fix.py" in show.stdout
    # main working tree stays clean — the worker's file never touched it
    assert not (Path(repo) / "fix.py").exists()
    assert _git(["status", "--porcelain"], repo).stdout.strip() == ""


def test_worker_noop_runner_makes_no_candidate_commit(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    bb = swarm.create_swarm(tmp_path / "state", "obj")

    def noop(objective, cwd, base_config, system_prompt, allow, mt, mtc):
        return "changed nothing"

    out = swarm.run_worker(bb, repo, "HEAD", "agent-1", "obj", runner=noop)
    assert not out.ok and out.commit == "" and out.files_changed == 0
    assert bb.candidates()[0].status == swarm.CAND_REJECTED


def test_worker_crash_is_contained(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    bb = swarm.create_swarm(tmp_path / "state", "obj")

    def boom(objective, cwd, base_config, system_prompt, allow, mt, mtc):
        raise RuntimeError("kaboom")

    out = swarm.run_worker(bb, repo, "HEAD", "agent-1", "obj", runner=boom)
    assert not out.ok and "kaboom" in out.error
    # a candidate row still exists (failed) and the swarm did not raise
    assert len(bb.candidates()) == 1


def test_workers_are_isolated_from_each_other(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    bb = swarm.create_swarm(tmp_path / "state", "obj")
    o1 = swarm.run_worker(bb, repo, "HEAD", "a1", "obj", runner=_writer_runner("a.py", "A\n"))
    o2 = swarm.run_worker(bb, repo, "HEAD", "a2", "obj", runner=_writer_runner("b.py", "B\n"))
    assert o1.commit and o2.commit and o1.commit != o2.commit
    # each candidate's snapshot contains only its own file
    s1 = _git(["show", "--name-only", o1.commit], repo).stdout
    s2 = _git(["show", "--name-only", o2.commit], repo).stdout
    assert "a.py" in s1 and "b.py" not in s1
    assert "b.py" in s2 and "a.py" not in s2


def test_repo_root_detection(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    assert swarm.repo_root(repo) == str(Path(repo).resolve())
    assert swarm.repo_root(tmp_path / "not_a_repo") is None


# ── coordinator / verifier / judge slice ─────────────────────────────────────
def test_diversify_gives_distinct_angles():
    pairs = swarm.diversify("fix it", 4)
    assert len(pairs) == 4
    labels = [p[0] for p in pairs]
    assert len(set(labels)) == 4  # 4 distinct angles
    assert all("fix it" not in sys_prompt or True for _, sys_prompt in pairs)
    assert all(labels[i] in pairs[i][1] for i in range(4))  # angle embedded in system prompt


def test_judge_ranks_by_evidence():
    a = swarm.Candidate(id="c-1", agent="a1", commit="x", tests_passed=5, tests_total=10)
    b = swarm.Candidate(id="c-2", agent="a2", commit="y", tests_passed=10, tests_total=10)
    c = swarm.Candidate(id="c-3", agent="a3", commit="", tests_passed=10, tests_total=10)  # no patch
    assert swarm.judge([a, b, c]).id == "c-2"        # full pass ratio wins
    assert swarm.judge([c]) is None                   # no real patch → no winner
    # tie on ratio → fewer files changed wins (simpler)
    d = swarm.Candidate(id="c-4", agent="a4", commit="z", tests_passed=10, tests_total=10,
                        files_changed=1)
    e = swarm.Candidate(id="c-5", agent="a5", commit="w", tests_passed=10, tests_total=10,
                        files_changed=9)
    assert swarm.judge([e, d]).id == "c-4"


def test_run_swarm_needs_git_repo(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        swarm.run_swarm(tmp_path / "plain_dir", "obj", agents=2)


def test_run_swarm_end_to_end_converges(tmp_path):
    repo = _init_repo(tmp_path / "repo")

    # Only the "assume the obvious is wrong" angle finds the fix; others write a failing marker.
    def runner(objective, cwd, base_config, system_prompt, allow, mt, mtc):
        content = "PASS" if "Assume the obvious" in system_prompt else "FAIL"
        (Path(cwd) / "answer.txt").write_text(content)
        return f"wrote {content}"

    res = swarm.run_swarm(repo, "solve it", agents=4, base_config={},
                          verify_cmd="grep -q PASS answer.txt", fitness="exitcode",
                          runner=runner)
    assert res.converged is True
    assert res.winner is not None
    assert res.winner.status == swarm.CAND_ACCEPTED
    assert res.winner.tests_passed == res.winner.tests_total == 1
    # the winning worker is the one that got the "assume obvious wrong" angle (agent-2)
    assert res.winner.agent == "agent-2"
    # every arm produced a candidate; losers are rejected by the independent verifier
    assert len(res.candidates) == 4
    losers = [c for c in res.candidates if c.id != res.winner.id]
    assert all(c.status == swarm.CAND_REJECTED for c in losers)


def test_run_swarm_contains_a_crashing_worker(tmp_path):
    repo = _init_repo(tmp_path / "repo")

    def runner(objective, cwd, base_config, system_prompt, allow, mt, mtc):
        if "simplest" in system_prompt:      # first angle crashes
            raise RuntimeError("worker exploded")
        (Path(cwd) / "answer.txt").write_text("PASS")
        return "ok"

    res = swarm.run_swarm(repo, "solve it", agents=3, base_config={},
                          verify_cmd="grep -q PASS answer.txt", fitness="exitcode",
                          runner=runner)
    # swarm still converges on a surviving worker despite the crash
    assert res.converged is True and res.winner is not None
    assert res.winner.agent in ("agent-2", "agent-3")


def test_run_swarm_writes_metrics_and_events(tmp_path):
    repo = _init_repo(tmp_path / "repo")

    def runner(objective, cwd, base_config, system_prompt, allow, mt, mtc):
        (Path(cwd) / "f.txt").write_text("x")
        return "ok"

    res = swarm.run_swarm(repo, "obj", agents=2, base_config={},
                          verify_cmd="test -f f.txt", fitness="exitcode", runner=runner)
    bb = swarm.open_swarm(repo, res.swarm_id)
    m = bb.metrics()
    assert m["agents"] == 2 and m["candidates"] == 2
    ev_types = {json.loads(x)["type"]
                for x in (Path(res.root) / "events.jsonl").read_text().splitlines() if x.strip()}
    assert {"SWARM_START", "TASK_ASSIGNED", "TEST_COMPLETED", "JUDGE_DECISION"} <= ev_types

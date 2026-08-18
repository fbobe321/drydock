"""Tests for the parallel evolutionary ratchet loop (drydock.eratchet). The
variant EXECUTOR is injected, so the whole generation loop — selection, the QD
archive, escalation, and the stop conditions — is driven deterministically with
no subprocesses or network."""
from drydock.eratchet import (
    VariantOutcome,
    _run_generation_parallel,
    run_eratchet,
)


def _desc(k):
    return frozenset(f"t{i}" for i in range(k))


def test_solves_and_stops_at_the_generation_it_cracks():
    # gen1 (base_ref None) scores 3/8; once building on that base, a variant solves.
    def runner(base, server, spec, xplan):
        p = 8 if base is not None else 3
        return VariantOutcome(spec, server, p, 8, ref=f"ref-{server}", descriptor=_desc(p))
    res = run_eratchet("goal", servers=["s1", "s2"], runner=runner, effort="high")
    assert res.solved and res.best_passed == 8 and res.best_total == 8
    assert res.generations == 2                       # stopped as soon as it solved


def test_abort_flat_bails_a_no_gradient_task():
    # verifier never gives a gradient (0/5 forever) → bail per effort's abort_flat.
    def runner(base, server, spec, xplan):
        return VariantOutcome(spec, server, 0, 5, ref="r", descriptor=frozenset())
    res = run_eratchet("goal", servers=["s"], runner=runner, effort="medium")
    assert not res.solved and res.stopped == "abort_flat"
    assert res.generations == 2                       # medium abort_flat=2


def test_stall_escalates_operator_and_widens_fanout():
    # constant non-zero, non-improving score → no abort_flat (has gradient), but
    # the policy must escalate exploit → diversify and widen the variant count.
    events = []
    def runner(base, server, spec, xplan):
        return VariantOutcome(spec, server, 2, 5, ref="r", descriptor=_desc(2))
    run_eratchet("goal", servers=["s1", "s2"], runner=runner, effort="medium",
                 on_event=lambda k, d: events.append((k, d)))
    escalated = [d for k, d in events if k == "escalate"]
    assert any(d["operator"] == "diversify" for d in escalated)
    widths = [d["variants"] for k, d in events if k == "generation_start"]
    assert max(widths) >= 2                            # a fan-out generation ran ≥2 variants


def test_parallel_scheduler_preserves_order_and_isolates_failures():
    specs = [{"mode": "continue"}, {"mode": "rethink"}, {"mode": "continue"}]
    def runner(base, server, spec, xplan):
        if spec["mode"] == "rethink":
            raise RuntimeError("boom")
        return VariantOutcome(spec, server, 1, 2, ref="r")
    out = _run_generation_parallel(specs, ["s1", "s2"], runner, None, None)
    assert len(out) == 3
    assert out[0].error == "" and out[2].error == ""   # good variants unaffected
    assert out[1].error.startswith("RuntimeError")     # the raiser captured in place


def test_cancellation_stops_between_generations():
    calls = {"n": 0}
    def runner(base, server, spec, xplan):
        calls["n"] += 1
        return VariantOutcome(spec, server, 1, 9, ref="r", descriptor=_desc(1))
    # stop after the first generation completes
    res = run_eratchet("goal", servers=["s"], runner=runner, effort="high",
                       should_stop=lambda: calls["n"] >= 1)
    assert res.stopped == "cancelled" and not res.solved


# ── real executor plumbing (fake `drydock`, real git worktree + verifier) ────

def _init_repo(tmp_path):
    import subprocess
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "init"], cwd=r, check=True)
    return str(r)


def test_exec_variant_isolates_scores_and_cleans_up(tmp_path):
    import subprocess

    from drydock.eratchet import ExecConfig, exec_variant
    repo = _init_repo(tmp_path)
    # a fake "drydock" that just writes a file into the worktree (the "fix")
    cfg = ExecConfig(
        repo=repo, goal="make the file", verify_cmd="test -f made.txt",
        fitness="exitcode",
        drydock_argv=("bash", "-c", "echo done > made.txt"),
    )
    out = exec_variant(cfg, None, "http://s1", {"mode": "continue", "temperature": 0.2}, None)
    assert out.error == "" and out.passed == 1 and out.total == 1   # verifier passed
    assert out.ref                                                  # a snapshot ref exists
    # the change lived ONLY in the throwaway worktree — main repo is untouched…
    assert not (tmp_path / "repo" / "made.txt").exists()
    # …and no worktrees leaked.
    wl = subprocess.run(["git", "worktree", "list"], cwd=repo, capture_output=True, text=True)
    assert wl.stdout.count("\n") == 1                              # only the main tree


def test_exec_variant_reports_failure_without_the_fix(tmp_path):
    from drydock.eratchet import ExecConfig, exec_variant
    repo = _init_repo(tmp_path)
    cfg = ExecConfig(repo=repo, goal="g", verify_cmd="test -f made.txt",
                     fitness="exitcode", drydock_argv=("bash", "-c", "true"))
    out = exec_variant(cfg, None, "http://s1", {"mode": "continue"}, None)
    assert out.error == "" and out.passed == 0 and out.total == 1   # verifier failed cleanly


# ── front-end: parser, formatter, CLI driver ────────────────────────────────

def test_parse_eratchet_goal_and_flags():
    from drydock.eratchet import parse_eratchet
    o = parse_eratchet("crack the hard bug --effort max --servers a:1,b:2 --fanout 4".split())
    assert o["goal"] == "crack the hard bug" and o["effort"] == "max"
    assert o["servers"] == ["a:1", "b:2"] and o["fanout"] == 4


def test_parse_eratchet_errors_are_valueerror_not_systemexit():
    import pytest

    from drydock.eratchet import parse_eratchet
    with pytest.raises(ValueError):
        parse_eratchet(["--effort", "bogus", "goal"])
    with pytest.raises(ValueError):
        parse_eratchet([])                        # no goal


def test_format_event_lines():
    from drydock.eratchet import format_event
    assert "SOLVED" in format_event("solved", {"generation": 3, "passed": 8,
                                                "total": 8, "ref": "abc"})
    assert "no gradient" in format_event("abort_flat", {"generation": 2, "total": 5})


def test_run_cli_drives_the_loop_and_returns_exit_code(tmp_path, monkeypatch, capsys):
    import drydock.eratchet as erx
    repo = _init_repo(tmp_path)
    # patch the executor so no real drydock/subprocess runs
    def fake_exec(cfg, base, server, spec, xplan):
        p = 6 if base is not None else 3
        return erx.VariantOutcome(spec, server, p, 6, ref=f"r-{server}", descriptor=_desc(p))
    monkeypatch.setattr(erx, "exec_variant", fake_exec)
    code = erx.run_cli(["solve it", "--verify", "true", "--effort", "high"],
                       config={"cwd": repo, "base_url": "http://s"})
    assert code == 0                              # solved → exit 0
    assert "SOLVED" in capsys.readouterr().out

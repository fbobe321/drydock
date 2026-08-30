"""Ratchet primitive: fitness scoring, the pawl decision logic, git checkpoint
round-trip, and arg parsing. Pure/stdlib — no TUI needed."""
import subprocess

import pytest

from drydock.ratchet import (
    GitCheckpoint,
    RatchetState,
    Verifier,
    continuation_prompt,
    detect_verifier,
    parse_ratchet_args,
    score_output,
)


# ───────────────────────── fitness scoring ─────────────────────────

@pytest.mark.parametrize("out,rc,exp", [
    ("===== 5 passed, 1 failed in 0.3s =====", 1, (5, 6)),      # pytest
    ("===== 8 passed in 0.1s =====", 0, (8, 8)),                # pytest all-green
    ("test result: ok. 5 passed; 0 failed; 0 ignored", 0, (5, 5)),  # cargo
    ("Tests: 1 failed, 4 passed, 5 total", 1, (4, 5)),          # jest (uses 'total')
    ("2 failed, 0 passed", 1, (0, 2)),                          # nothing green
])
def test_score_auto(out, rc, exp):
    assert score_output(out, "auto", rc) == exp


def test_score_auto_falls_back_to_exitcode_when_unparseable():
    assert score_output("Build succeeded.", "auto", 0) == (1, 1)
    assert score_output("boom", "auto", 1) == (0, 1)


def test_score_exitcode_mode():
    assert score_output("whatever", "exitcode", 0) == (1, 1)
    assert score_output("whatever", "exitcode", 2) == (0, 1)


def test_score_custom_regex_two_groups():
    assert score_output("checks: 3/7 passing", r"(\d+)/(\d+) passing", 1) == (3, 7)


def test_verifier_runs_real_command():
    assert Verifier("exit 0", mode="exitcode").run().solved is True
    r = Verifier("echo '2 passed, 2 failed'", mode="auto").run()
    assert (r.passed, r.total) == (2, 4)
    assert r.solved is False


# ───────────────────────── the pawl (decision logic) ─────────────────────────

def test_first_round_always_pawls_even_at_zero():
    # best starts at the -1 sentinel, so even 0/7 locks in on round 1
    st = RatchetState(goal="x", max_rounds=6)
    assert st.record(0, 7, "sha0") == "pawl"
    assert st.best_passed == 0 and st.best_ref == "sha0"


def test_improvement_pawls_regression_rolls_back():
    st = RatchetState(goal="x")
    st.record(5, 6, "a")                       # round 1: 5/6 pawl
    assert st.record(5, 6, "b") == "rollback"  # round 2: no gain → discard
    assert st.best_ref == "a"                  # best unchanged
    assert st.record(6, 6, "c") == "solved"    # round 3: closes it
    assert st.best_ref == "c"


def test_monotonic_best_never_decreases():
    st = RatchetState(goal="x")
    st.record(3, 8, "a")
    st.record(1, 8, "b")   # a bad round
    assert st.best_passed == 3 and st.best_ref == "a"


def test_exhausted():
    st = RatchetState(goal="x", max_rounds=2)
    st.record(0, 3, "a")
    assert st.exhausted() is False
    st.record(0, 3, "b")
    assert st.exhausted() is True


def test_continuation_prompt_mentions_state_and_preservation():
    p = continuation_prompt("solve it", 5, 6)
    assert "5/6" in p and "PRESERVED" in p and "solve it" in p


# ───────────────────────── git checkpoint round-trip ─────────────────────────

def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def test_git_checkpoint_snapshot_restore(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "a.txt").write_text("v1\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "init"], repo)

    cp = GitCheckpoint(str(repo))
    assert cp.available()

    # progress: modify a tracked file + add an untracked one, then snapshot (the pawl)
    (repo / "a.txt").write_text("v2\n")
    (repo / "b.txt").write_text("new\n")
    good = cp.snapshot("best 2/2")
    assert good

    # a regressed round: clobber a.txt, delete b.txt, add junk
    (repo / "a.txt").write_text("BROKEN\n")
    (repo / "b.txt").unlink()
    (repo / "junk.txt").write_text("noise\n")

    # roll back to the good snapshot
    assert cp.restore(good)
    assert (repo / "a.txt").read_text() == "v2\n"      # tracked change restored
    assert (repo / "b.txt").read_text() == "new\n"     # untracked file restored
    assert not (repo / "junk.txt").exists()            # file added since is pruned


def test_git_checkpoint_unavailable_outside_repo(tmp_path):
    assert GitCheckpoint(str(tmp_path)).available() is False


# ───────────────────────── arg parsing ─────────────────────────

def test_parse_basic():
    goal, verify, rounds, fitness, effort = parse_ratchet_args(
        'make the suite pass --verify "pytest -q" --rounds 8 --fitness auto --effort high')
    assert goal == "make the suite pass"
    assert verify == "pytest -q"
    assert rounds == 8 and fitness == "auto" and effort == "high"


def test_parse_defaults():
    goal, verify, rounds, fitness, effort = parse_ratchet_args('fix it --verify "go test ./..."')
    assert goal == "fix it" and verify == "go test ./..."
    assert rounds == 0 and fitness == "" and effort == ""  # unspecified → caller resolves


def test_parse_bare_goal_leaves_verify_empty_for_autodetect():
    goal, verify, rounds, fitness, effort = parse_ratchet_args("make the failing test pass")
    assert goal == "make the failing test pass"
    assert verify == "" and fitness == "" and rounds == 0 and effort == ""


@pytest.mark.parametrize("bad", [
    "",                                       # no goal
    "--verify",                               # flag needs a value
    'goal --verify "x" --rounds notanumber',  # bad number
    'goal --verify "x" trailing',             # positional after a flag
    'goal --effort ludicrous',                # invalid effort level
])
def test_parse_errors(bad):
    with pytest.raises(ValueError):
        parse_ratchet_args(bad)


def test_parse_rounds_clamped():
    _, _, rounds, _, _ = parse_ratchet_args('g --verify "x" --rounds 999')
    assert rounds == 30


# ── effort spectrum ──

def test_effort_low_is_plain_pawl():
    from drydock.ratchet import effort_profile, policy_for
    p = effort_profile("low")
    assert p["ladder"] == ("exploit",)            # never escalates → pure hill-climber
    pol = policy_for("low")
    for _ in range(6):
        assert pol.next_operator(improved=False) == "exploit"   # stuck on exploit forever


def test_effort_medium_caps_at_diversify():
    from drydock.ratchet import policy_for
    pol = policy_for("medium")   # ladder = (exploit, diversify), patience 2
    ops = {pol.next_operator(improved=False) for _ in range(10)}
    assert ops <= {"exploit", "diversify"} and "fanout" not in ops and "crossover" not in ops


def test_effort_high_unlocks_full_ladder_and_scales_fanout():
    from drydock.ratchet import effort_profile, policy_for
    assert effort_profile("high")["ladder"][-1] == "restart"
    assert effort_profile("max")["fanout"] == 5
    pol = policy_for("max")   # fanout 5 → a fan-out round runs 5 variants (exploit + 4)
    assert len(pol.variant_specs("fanout")) == 5
    assert pol.variant_specs("fanout")[0]["mode"] == "continue"   # still exploit-first


def test_effort_unknown_defaults_high():
    from drydock.ratchet import effort_profile
    assert effort_profile("bogus") == effort_profile("high")


# ───────────────────────── verifier auto-detection ─────────────────────────

def test_detect_verifier_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert detect_verifier(str(tmp_path)) == ("pytest -q", "auto")


def test_detect_verifier_rust_beats_python(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\n")
    (tmp_path / "tests").mkdir()
    assert detect_verifier(str(tmp_path)) == ("cargo test", "auto")


def test_detect_verifier_go(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    assert detect_verifier(str(tmp_path)) == ("go test ./...", "exitcode")


def test_detect_verifier_npm_skips_placeholder(tmp_path):
    import json
    (tmp_path / "package.json").write_text(json.dumps(
        {"scripts": {"test": "echo \"Error: no test specified\" && exit 1"}}))
    assert detect_verifier(str(tmp_path)) is None
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    assert detect_verifier(str(tmp_path)) == ("npm test --silent", "auto")


def test_detect_verifier_make_test_target(tmp_path):
    (tmp_path / "Makefile").write_text("build:\n\tgcc x.c\ntest:\n\t./a.out\n")
    assert detect_verifier(str(tmp_path)) == ("make test", "exitcode")


def test_detect_verifier_none(tmp_path):
    assert detect_verifier(str(tmp_path)) is None


# ═══════════════════════ evolutionary core ═══════════════════════

from drydock.ratchet import (  # noqa: E402
    Archive,
    Candidate,
    VariationPolicy,
    diversify_prompt,
    dominates,
    parse_descriptor,
    pareto_front,
    plan_crossover,
)


def _c(id, f, t, desc=None, **kw):
    return Candidate(id=id, fitness=f, total=t,
                     descriptor=frozenset(desc) if desc is not None else None, **kw)


# ── descriptor parsing ──

def test_parse_descriptor_pytest_verbose():
    out = "tests/test_a.py::test_x PASSED\ntests/test_a.py::test_y FAILED\ntest_a.py::test_z PASSED"
    assert parse_descriptor(out) == frozenset({"tests/test_a.py::test_x", "test_a.py::test_z"})


def test_parse_descriptor_none_when_unparseable():
    assert parse_descriptor("Build OK") is None


# ── QD archive (rec 1) ──

def test_archive_niches_by_behaviour_not_count():
    a = Archive()
    assert a.consider(_c("A", 2, 4, ["1", "2"])) == "new-niche"
    assert a.consider(_c("B", 2, 4, ["3", "4"])) == "new-niche"   # same count, different checks → kept
    assert len(a.all()) == 2                                       # diversity preserved


def test_archive_improves_within_niche_only():
    a = Archive()
    a.consider(_c("A", 2, 4, ["1", "2"]))
    assert a.consider(_c("A2", 2, 4, ["1", "2"], generalizes=0.5)) == "improved"  # better on 2nd obj
    assert a.consider(_c("A3", 1, 4, ["1", "2"])) == "rejected"
    assert a.best().id == "A2"


def test_archive_count_bucket_fallback_when_no_descriptor():
    a = Archive()
    a.consider(_c("A", 3, 6))
    assert a.consider(_c("B", 3, 6)) == "rejected"     # same count bucket, not better
    assert a.consider(_c("C", 4, 6)) == "new-niche"    # different count bucket


def test_archive_reports_solved():
    a = Archive()
    a.consider(_c("A", 5, 6, ["1", "2", "3", "4", "5"]))
    assert a.solved() is None
    a.consider(_c("B", 6, 6, ["1", "2", "3", "4", "5", "6"]))
    assert a.solved().id == "B"


# ── multi-objective / Pareto (rec 6) ──

def test_dominates_and_pareto_front():
    hi = _c("hi", 6, 6, cost=5.0)
    cheap = _c("cheap", 5, 6, cost=1.0)
    dud = _c("dud", 4, 6, cost=9.0)
    assert dominates(hi, dud)
    assert not dominates(hi, cheap)   # hi wins fitness but loses cost → non-dominated pair
    front = pareto_front([hi, cheap, dud])
    ids = {c.id for c in front}
    assert ids == {"hi", "cheap"} and "dud" not in ids


# ── crossover (rec 2) ──

def test_complementary_pairs_and_plan():
    a = Archive()
    a.consider(_c("A", 3, 6, ["1", "2", "3"]))
    a.consider(_c("B", 3, 6, ["1", "4", "5"]))
    pairs = a.complementary_pairs()
    assert len(pairs) == 1
    plan = plan_crossover(pairs[0][0], pairs[0][1], gen=2)
    assert set(plan["target"]) == {"1", "2", "3", "4", "5"}
    assert set(plan["wants"]) in ({"4", "5"}, {"2", "3"})       # missing checks to graft
    assert plan["generation"] == 2 and len(plan["parents"]) == 2


def test_no_crossover_when_subsumed():
    a = _c("A", 3, 6, ["1", "2", "3"])
    b = _c("B", 2, 6, ["1", "2"])         # b ⊂ a → no new union
    assert plan_crossover(a, b, 1) is None


# ── variation policy (rec 3) ──

def test_variation_policy_escalates_then_resets():
    p = VariationPolicy(patience=1)
    assert p.next_operator(improved=True) == "exploit"
    assert p.next_operator(improved=False) == "diversify"
    assert p.next_operator(improved=False) == "fanout"
    # crossover rung falls back to restart when nothing to recombine
    assert p.next_operator(improved=False, have_crossover=False) == "restart"
    # a win collapses the ladder back to exploit
    assert p.next_operator(improved=True) == "exploit"


def test_variation_policy_uses_crossover_when_available():
    p = VariationPolicy(patience=1)
    p.next_operator(False)  # diversify
    p.next_operator(False)  # fanout
    assert p.next_operator(improved=False, have_crossover=True) == "crossover"
    assert p.params_for("fanout")["variants"] == p.fanout


def test_diversify_prompt_tells_model_not_to_repeat():
    d = diversify_prompt("solve it", 5, 6, "diversify")
    assert "DIFFERENT" in d and "5/6" in d and "PRESERVED" in d


def test_variation_policy_elitism_exploit_always_first():
    # every operator's variants START with the steady low-temp exploit move, so a
    # round can never underperform the plain pawl (the fix for eratchet regressing)
    p = VariationPolicy()
    for op in VariationPolicy.LADDER:
        specs = p.variant_specs(op)
        assert specs[0] == {"mode": "continue", "temperature": 0.2}, op
        assert len(specs) >= 1
    # exploration adds EXTRA variants, never replaces exploit
    assert len(p.variant_specs("diversify")) == 2
    assert len(p.variant_specs("exploit")) == 1
    params = p.params_for("fanout")
    assert params["specs"][0]["mode"] == "continue" and params["variants"] == len(params["specs"])


def test_variation_policy_default_patience_grinds_before_escalating():
    # default patience=2: two stalls on exploit before diversifying (was 1)
    p = VariationPolicy()
    assert p.next_operator(improved=False) == "exploit"   # stall 1 → still grind
    assert p.next_operator(improved=False) == "diversify"  # stall 2 → escalate


def test_every_effort_profile_exposes_abort_flat():
    # the /ratchet driver reads prof["abort_flat"] to bail a no-gradient task
    from drydock.ratchet import EFFORT_LEVELS, effort_profile
    for lvl in EFFORT_LEVELS:
        p = effort_profile(lvl)
        assert "abort_flat" in p and isinstance(p["abort_flat"], int)
    assert effort_profile("max")["abort_flat"] == 0        # 'max' never bails


def test_diversify_prompt_varies_by_operator():
    # the driver uses the operator to make a stalled round change strategy
    from drydock.ratchet import diversify_prompt
    exploit = diversify_prompt("goal", 2, 5, "exploit")     # falls back to continuation
    div = diversify_prompt("goal", 2, 5, "diversify")
    restart = diversify_prompt("goal", 2, 5, "restart")
    assert "2/5" in div and "2/5" in restart
    assert "DIFFERENT strategy" in div and "STALLED" in div
    # `restart` used to just SAY "first principles", which a model mostly answers by
    # re-wording its previous plan. It now asks the questions explicitly (measured
    # better on stuck runs, 2026-08-30) — so assert the substance, not the slogan.
    assert "ASSUMING" in restart and "SIMPLEST" in restart and "PREVENTS" in restart
    assert div != exploit and restart != exploit and div != restart

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
    goal, verify, rounds, fitness = parse_ratchet_args(
        'make the suite pass --verify "pytest -q" --rounds 8 --fitness auto')
    assert goal == "make the suite pass"
    assert verify == "pytest -q"
    assert rounds == 8 and fitness == "auto"


def test_parse_defaults():
    goal, verify, rounds, fitness = parse_ratchet_args('fix it --verify "go test ./..."')
    assert goal == "fix it" and verify == "go test ./..."
    assert rounds == 6 and fitness == ""  # unspecified → caller resolves


def test_parse_bare_goal_leaves_verify_empty_for_autodetect():
    goal, verify, rounds, fitness = parse_ratchet_args("make the failing test pass")
    assert goal == "make the failing test pass"
    assert verify == "" and fitness == "" and rounds == 6


@pytest.mark.parametrize("bad", [
    "",                                       # no goal
    "--verify",                               # flag needs a value
    'goal --verify "x" --rounds notanumber',  # bad number
    'goal --verify "x" trailing',             # positional after a flag
])
def test_parse_errors(bad):
    with pytest.raises(ValueError):
        parse_ratchet_args(bad)


def test_parse_rounds_clamped():
    _, _, rounds, _ = parse_ratchet_args('g --verify "x" --rounds 999')
    assert rounds == 20


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

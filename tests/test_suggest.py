"""Recommended-next-command suggestion logic (priority-ordered, None when noisy)."""
from drydock.suggest import suggest_next_command as s


def test_priority_and_cases():
    base = dict(ctx_pct=10, wrote_files=False, ran_bash=False, had_error=False,
                in_git=False, plan_remaining=False)
    assert s(**{**base, "ctx_pct": 80}) == "/compact"          # ctx wins
    assert s(**{**base, "ctx_pct": 80, "had_error": True}) == "/compact"
    assert s(**{**base, "had_error": True}) == "fix the error above"
    assert s(**{**base, "plan_remaining": True}) == "continue"
    assert s(**{**base, "wrote_files": True, "in_git": True}) == "git diff"
    assert s(**{**base, "wrote_files": True}) == "run the tests"
    assert s(**{**base, "ran_bash": True}) is None             # no clutter
    assert s(**base) is None


def test_error_beats_plan_and_writes():
    assert s(ctx_pct=10, wrote_files=True, ran_bash=True, had_error=True,
             in_git=True, plan_remaining=True) == "fix the error above"


def test_is_slash_command_distinguishes_paths():
    from drydock.tui.app import is_slash_command as c
    assert c("/help") and c("/ml-train foo") and c("/ask!") and c("/compact")
    # file paths / messages starting with a path are NOT commands
    assert not c("/app/env.py defines a gridworld")
    assert not c("/report.tex")
    assert not c("/data/file.h5 holds predictions")
    assert not c("Train a model") and not c("/")

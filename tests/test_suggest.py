"""Ghost-text prompt suggestion (Claude-Code-style, accepted with Tab).

The suggestion renders into the PromptArea placeholder (inline ghost text). Two
sources: a bounded contextual model call (suggest_reply_llm) and a zero-cost
heuristic fallback (suggest_next_command). Only pure logic is tested here; the
model call and TUI rendering are exercised by hand in a tmux TUI session.
"""
from drydock.suggest import (
    _clean,
    build_suggest_prompt,
    ends_with_question,
    suggest_reply_llm,
)
from drydock.suggest import suggest_next_command as s


def test_priority_and_cases():
    base = dict(ctx_pct=10, wrote_files=False, ran_bash=False, had_error=False,
                in_git=False, plan_remaining=False)
    assert s(**{**base, "ctx_pct": 80}) == "/compact"          # ctx wins
    assert s(**{**base, "ctx_pct": 80, "had_error": True}) == "/compact"
    assert s(**{**base, "asked_question": True}) == "yes, go ahead"
    assert s(**{**base, "had_error": True}) == "fix the error above"
    assert s(**{**base, "plan_remaining": True}) == "continue"
    assert s(**{**base, "wrote_files": True, "in_git": True}) == "review the changes with git diff"
    assert s(**{**base, "wrote_files": True}) == "run the tests to verify"
    assert s(**{**base, "ran_bash": True}) is None             # no clutter
    assert s(**base) is None


def test_error_beats_plan_and_writes():
    assert s(ctx_pct=10, wrote_files=True, ran_bash=True, had_error=True,
             in_git=True, plan_remaining=True) == "fix the error above"


def test_question_detection():
    assert ends_with_question("Should I proceed with the refactor?")
    assert ends_with_question("Which approach do you prefer")
    assert ends_with_question("Want me to add tests too?")
    assert not ends_with_question("I fixed the bug and ran the tests.")
    assert not ends_with_question("")


def test_clean_strips_thinking_and_quotes_and_keeps_one_line():
    assert _clean("<|channel|>thinking<|channel|>continue") == "continue"
    assert _clean('  "run the tests"  \n more') == "run the tests"
    assert _clean("") == ""
    assert _clean("line one\nline two") == "line one"


def test_build_suggest_prompt():
    system, user = build_suggest_prompt("Do you want me to add tests?")
    assert "next reply" in system.lower()
    assert "NONE" in system                    # escape hatch for no-suggestion
    assert "add tests" in user


def test_llm_suggest_is_defensive():
    # unreachable endpoint → None (never raise), so the TUI falls back to heuristic.
    assert suggest_reply_llm("Should I proceed?",
                             {"base_url": "http://127.0.0.1:9/v1", "model": "x"}) is None
    assert suggest_reply_llm("", {}) is None
    assert suggest_reply_llm("   ", {}) is None


def test_is_slash_command_distinguishes_paths():
    from drydock.tui.app import is_slash_command as c
    assert c("/help") and c("/ml-train foo") and c("/ask!") and c("/compact")
    # file paths / messages starting with a path are NOT commands
    assert not c("/app/env.py defines a gridworld")
    assert not c("/report.tex")
    assert not c("/data/file.h5 holds predictions")
    assert not c("Train a model") and not c("/")

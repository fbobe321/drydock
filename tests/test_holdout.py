"""Holdout verification (drydock/holdout.py) — mitigation 1 for the reward hacking in
MCR PRD Appendix A.4: confirm a claimed solve with a check the agent never saw."""

from drydock.holdout import (
    HoldoutResult,
    confirm,
    corroboration_label,
    extract_holdout,
)


# ── argument extraction keeps parse_ratchet_args untouched ──────────────────

def test_extract_holdout_pulls_the_flag_out():
    rest, cmd = extract_holdout('fix the parser --rounds 3 --holdout "pytest holdout/" --effort high')
    assert cmd == "pytest holdout/"
    assert "--holdout" not in rest and "--rounds 3" in rest and "--effort high" in rest


def test_extract_holdout_absent_is_passthrough():
    rest, cmd = extract_holdout("fix the parser --rounds 3")
    assert cmd == "" and "fix the parser" in rest


def test_extract_holdout_trailing_flag_without_value():
    rest, cmd = extract_holdout("goal --holdout")
    assert cmd == "" and "--holdout" in rest        # malformed: left alone, not eaten


def test_extract_holdout_survives_unbalanced_quotes():
    rest, cmd = extract_holdout('goal --holdout "unclosed')
    assert cmd == "" and rest                        # never raises


# ── confirming a claim ──────────────────────────────────────────────────────

def test_not_configured_is_not_a_rejection():
    r = confirm("", cwd=".")
    assert r.ran is False and r.agreed is False
    assert r.verdict() == "not configured"


def test_holdout_agreeing_confirms(tmp_path):
    (tmp_path / "test_t.py").write_text("def test_ok():\n    assert True\n")
    r = confirm("python -m pytest -q", str(tmp_path))
    assert r.ran and r.agreed and r.passed == r.total == 1
    assert "confirmed 1/1" in r.verdict()


def test_holdout_disagreeing_rejects_the_claim(tmp_path):
    """The A.4 case: primary suite says solved, holdout says otherwise."""
    (tmp_path / "test_t.py").write_text(
        "def test_a():\n    assert True\n\ndef test_b():\n    assert False\n")
    r = confirm("python -m pytest -q", str(tmp_path))
    assert r.ran and r.agreed is False and r.inconclusive is False
    assert "REJECTED" in r.verdict() and r.passed < r.total


def test_unrunnable_holdout_is_inconclusive_not_a_rejection(tmp_path):
    """Fail OPEN on infrastructure failure — a holdout that cannot run is evidence of
    nothing and must not veto honest work."""
    r = confirm("definitely-not-a-real-command-xyz", str(tmp_path))
    assert r.ran and r.agreed is False and r.inconclusive is True
    assert "inconclusive" in r.verdict()


def test_holdout_with_nothing_gradeable_is_inconclusive(tmp_path):
    r = confirm("true", str(tmp_path), fitness="regex:(?!x)(?!y)")
    assert r.inconclusive is True or r.agreed is True   # never a silent rejection
    assert "REJECTED" not in r.verdict()


def test_corroboration_label_names_the_actual_command():
    res = HoldoutResult(ran=True, agreed=True, passed=9, total=9)
    label = corroboration_label("pytest holdout/", res)
    assert "pytest holdout/" in label and "9/9" in label

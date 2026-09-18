"""The Ratchet -> Modular Context Runtime bridge (drydock/context_ratchet.py):
a pawl becomes a ContextCheckpoint tied to the git ref + verifier score; a rollback
becomes an evidence-backed tombstone. See docs/modular_context_runtime_prd.md §11."""
from drydock.context_ratchet import ContextRatchet, failed_checks
from drydock.context_runtime import ARCHIVED, PINNED, TOMBSTONED

PYTEST_OUT = """\
=================================== FAILURES ===================================
short test summary info
FAILED tests/test_parser.py::test_nested - AssertionError
FAILED tests/test_parser.py::test_escape - ValueError
ERROR tests/test_boot.py::test_import
3 failed in 0.4s
"""


def test_failed_checks_parses_pytest_failures():
    assert failed_checks(PYTEST_OUT) == [
        "tests/test_parser.py::test_nested",
        "tests/test_parser.py::test_escape",
        "tests/test_boot.py::test_import",
    ]


def test_failed_checks_dedupes_and_limits():
    out = "\n".join(["FAILED a::b"] * 3 + [f"FAILED x::t{i}" for i in range(30)])
    got = failed_checks(out, limit=5)
    assert got[0] == "a::b" and len(got) == 5 and len(set(got)) == 5


def test_failed_checks_empty_input():
    assert failed_checks("") == [] and failed_checks("all good") == []


def test_objective_is_pinned_at_construction(tmp_path):
    cr = ContextRatchet(str(tmp_path), run_id="r1", goal="make the parser handle nesting")
    m = cr.store.get("ctx://task/objective")
    assert m.residency == PINNED and m.verified is True
    assert m.body == "make the parser handle nesting"


def test_pawl_takes_a_checkpoint_tied_to_git_ref_and_fitness(tmp_path):
    cr = ContextRatchet(str(tmp_path), run_id="r2", goal="g")
    out = cr.on_round(round_no=3, action="pawl", passed=14, total=22, git_ref="abc1234")
    assert out["checkpoint"] and not out["tombstone"]
    rec = cr.checkpoint.get(out["checkpoint"])
    assert rec["git_ref"] == "abc1234"
    assert abs(rec["fitness"] - 14 / 22) < 1e-9
    assert rec["label"] == "round 3 14/22"


def test_solved_also_checkpoints(tmp_path):
    cr = ContextRatchet(str(tmp_path), run_id="r3")
    assert cr.on_round(round_no=5, action="solved", passed=22, total=22)["checkpoint"]


def test_rollback_tombstones_the_attempt_with_verifier_evidence(tmp_path):
    cr = ContextRatchet(str(tmp_path), run_id="r4", goal="g")
    out = cr.on_round(round_no=2, action="rollback", passed=9, total=22,
                      verifier_output=PYTEST_OUT, approach="rewrite the parser with regex")
    assert out["tombstone"] == "ctx://tombstone/attempt.r2"
    t = cr.store.get(out["tombstone"])
    assert t.residency == TOMBSTONED
    assert t.verified is True                      # evidence-backed -> promotable (§18/A.3)
    assert "rewrite the parser with regex" in t.body
    assert "tests/test_parser.py::test_nested" in t.body
    assert "scored 9/22" in t.body
    # the full attempt is archived, not destroyed (§2)
    assert cr.store.get("ctx://attempt/r2").residency == ARCHIVED


def test_rollback_without_verifier_output_still_records_the_score(tmp_path):
    cr = ContextRatchet(str(tmp_path), run_id="r5")
    out = cr.on_round(round_no=1, action="rollback", passed=0, total=22)
    t = cr.store.get(out["tombstone"])
    assert t.verified is True and '"passed": 0' in t.body


def test_tombstone_is_far_smaller_than_the_attempt(tmp_path):
    cr = ContextRatchet(str(tmp_path), run_id="r6")
    fat = "TRANSCRIPT " * 3000
    out = cr.on_round(round_no=1, action="rollback", passed=1, total=9, approach=fat)
    t = cr.store.get(out["tombstone"])
    attempt = cr.store.get("ctx://attempt/r1")
    assert t.token_size < attempt.token_size / 5


def test_on_round_never_raises_on_bad_input(tmp_path):
    cr = ContextRatchet(str(tmp_path), run_id="r7")
    assert cr.on_round(round_no=1, action="rollback", passed=1, total=0) is not None
    assert cr.on_round(round_no=1, action="nonsense", passed=1, total=2)["checkpoint"] == ""


def test_summary_counts_what_the_run_accumulated(tmp_path):
    cr = ContextRatchet(str(tmp_path), run_id="r8", goal="g")
    cr.on_round(round_no=1, action="pawl", passed=5, total=22, git_ref="aaa")
    cr.on_round(round_no=2, action="rollback", passed=3, total=22,
                verifier_output=PYTEST_OUT)
    s = cr.summary()
    assert s["run_id"] == "r8" and s["checkpoints"] == 1 and s["tombstones"] == 1
    assert s["modules"] >= 3 and s["tokens_stored"] > 0


def test_a_run_is_isolated_to_its_own_store(tmp_path):
    a = ContextRatchet(str(tmp_path), run_id="runA", goal="A")
    b = ContextRatchet(str(tmp_path), run_id="runB", goal="B")
    a.on_round(round_no=1, action="rollback", passed=1, total=2)
    assert b.summary()["tombstones"] == 0
    assert b.store.get("ctx://task/objective").body == "B"

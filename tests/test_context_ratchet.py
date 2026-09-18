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


# ════════ Appendix A.4: a passing verifier is a WEAK claim ════════

from drydock.context_runtime import BRANCH, PROJECT, TASK  # noqa: E402


def test_a_pawl_records_the_claim_as_verifier_passed_not_verified(tmp_path):
    cr = ContextRatchet(str(tmp_path), run_id="a1")
    out = cr.on_round(round_no=1, action="pawl", passed=10, total=10, git_ref="deadbeef")
    m = cr.store.get(out["claim"])
    assert m.verifier_passed is True
    assert m.verified is False            # the verifier's word alone is not corroboration
    assert "10/10" in m.body


def test_a_verifier_pass_cannot_buy_promotion_past_branch(tmp_path):
    """The reward-hack scenario: a 10/10 that computes nothing must not become
    PROJECT knowledge on the verifier's say-so."""
    cr = ContextRatchet(str(tmp_path), run_id="a2")
    out = cr.on_round(round_no=1, action="solved", passed=10, total=10)
    cid = out["claim"]
    assert cr.store.get(cid).scope == BRANCH      # a claim is born branch-local
    assert cr.store.promote(cid, TASK) is None    # …and stays there without corroboration
    assert cr.store.promote(cid, PROJECT) is None
    assert cr.store.get(cid).scope == BRANCH


def test_corroboration_is_the_only_route_to_verified(tmp_path):
    cr = ContextRatchet(str(tmp_path), run_id="a3")
    cid = cr.on_round(round_no=1, action="solved", passed=10, total=10)["claim"]
    assert cr.store.corroborate(cid, by="") is None          # must say what corroborated
    m = cr.store.corroborate(cid, by="holdout suite the agent never saw")
    assert m.verified is True and m.corroborated_by.startswith("holdout")
    assert cr.store.promote(cid, PROJECT) is not None         # now it may climb


def test_record_verifier_pass_never_sets_verified(tmp_path):
    from drydock.context_runtime import ContextModule, ContextStore
    s = ContextStore(root=str(tmp_path), name="a4")
    s.put(ContextModule(context_id="ctx://k/claim"))
    m = s.record_verifier_pass("ctx://k/claim")
    assert m.verifier_passed is True and m.verified is False
    assert s.record_verifier_pass("ctx://missing/x") is None


def test_failure_evidence_stays_trustworthy_asymmetry(tmp_path):
    """Tombstones assert a FAILURE; reward hacking fabricates passes, not failures, so
    evidence-backed tombstones remain verified and promotable."""
    cr = ContextRatchet(str(tmp_path), run_id="a5")
    out = cr.on_round(round_no=1, action="rollback", passed=2, total=10,
                      verifier_output=PYTEST_OUT)
    t = cr.store.get(out["tombstone"])
    assert t.verified is True and t.verifier_passed is False

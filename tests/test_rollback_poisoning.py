"""Gate 4 (validation PRD §20) — a rollback must revert CODE and KNOWLEDGE together.

"If code rolls back but knowledge doesn't, Ratchet can poison itself." The ratchet's
rollback path called GitCheckpoint.restore() only, so a discarded round's false
hypothesis stayed resident and fed the next attempt. These tests pin the paired
transaction: the git ref of an accepted tooth is the join key to its ContextCheckpoint.
"""
from drydock.context_ratchet import ContextRatchet
from drydock.context_runtime import ARCHIVED, ContextModule

FALSE_HYPOTHESIS = "ctx://hypothesis/regex-is-fine"


def _run_to_seven(tmp_path):
    """A tooth accepted at 7/10, with its context checkpointed alongside git ref sha7."""
    cr = ContextRatchet(str(tmp_path), run_id="poison", goal="fix the parser")
    cr.store.put(ContextModule(context_id="ctx://knowledge/verified",
                               body="parser must stay recursive", verified=True))
    cr.on_round(round_no=1, action="pawl", passed=7, total=10, git_ref="sha7")
    return cr


def test_rollback_removes_a_false_hypothesis_added_after_the_checkpoint(tmp_path):
    cr = _run_to_seven(tmp_path)
    # a speculative round invents something wrong and scores worse
    cr.store.put(ContextModule(context_id=FALSE_HYPOTHESIS,
                               body="the regex approach is fine, tests are wrong"))
    cr.on_round(round_no=2, action="rollback", passed=5, total=10,
                verifier_output="FAILED tests/test_p.py::test_nested\n")
    assert cr.store.get(FALSE_HYPOTHESIS).residency != ARCHIVED    # present pre-rollback

    assert cr.rollback_to("sha7") is True
    m = cr.store.get(FALSE_HYPOTHESIS)
    assert m is not None and m.residency == ARCHIVED, (
        "a hypothesis born after the accepted tooth must not survive the rollback")


def test_rollback_preserves_knowledge_that_predates_the_checkpoint(tmp_path):
    """Rolling back must not throw away what the tooth actually earned."""
    cr = _run_to_seven(tmp_path)
    cr.store.put(ContextModule(context_id=FALSE_HYPOTHESIS, body="wrong"))
    cr.on_round(round_no=2, action="rollback", passed=5, total=10)
    cr.rollback_to("sha7")
    kept = cr.store.get("ctx://knowledge/verified")
    assert kept.residency != ARCHIVED and "recursive" in kept.body
    assert cr.store.get("ctx://task/objective").residency == "pinned"


def test_rollback_reverts_an_edit_made_after_the_checkpoint(tmp_path):
    """Not just additions — a module EDITED during the bad round must revert too."""
    cr = _run_to_seven(tmp_path)
    cr.store.put(ContextModule(context_id="ctx://knowledge/verified",
                               body="CORRUPTED: parser should be regex"))
    cr.on_round(round_no=2, action="rollback", passed=5, total=10)
    cr.rollback_to("sha7")
    assert "recursive" in cr.store.get("ctx://knowledge/verified").body


def test_rollback_to_an_unknown_ref_is_a_noop_not_a_wipe(tmp_path):
    cr = _run_to_seven(tmp_path)
    assert cr.rollback_to("no-such-sha") is False
    assert cr.rollback_to("") is False
    assert cr.store.get("ctx://knowledge/verified") is not None


def test_the_git_ref_is_the_join_key_between_the_two_checkpoints(tmp_path):
    """The pairing must be explicit: a tooth's git ref finds its ContextCheckpoint."""
    cr = _run_to_seven(tmp_path)
    cr.on_round(round_no=2, action="pawl", passed=9, total=10, git_ref="sha9")
    assert cr.rollback_to("sha9") is True
    assert cr.rollback_to("sha7") is True          # can reach an earlier tooth too

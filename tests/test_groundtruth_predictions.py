"""Tests for the ground-truth ledger and prediction register (first-principles loop,
steps 2/6 and 8). Both are advisory artifacts, so the tests assert the RANKING and the
never-raise contract — the two things that make them worth having."""

from drydock.groundtruth import Ledger, FACT, ASSUMPTION
from drydock.predictions import Register, CONFIRMED, REFUTED, OPEN


# ── ledger: the ranking is the product ───────────────────────────────────────
def test_next_test_picks_highest_impact_not_cheapest():
    lg = Ledger()
    lg.add("trivial detail", ASSUMPTION, impact=1, cost=0)      # cheapest
    lg.add("approach may be invalid", ASSUMPTION, impact=3, cost=3)  # decisive
    nxt = lg.next_test()
    assert nxt is not None and nxt.impact == 3, \
        "must attack the decision-changing unknown, not the easy one"


def test_cost_only_breaks_ties_between_equal_impact():
    lg = Ledger()
    a = lg.add("expensive check", ASSUMPTION, impact=3, cost=3)
    b = lg.add("cheap check", ASSUMPTION, impact=3, cost=0)
    assert lg.next_test().id == b.id
    assert a.impact == b.impact


def test_zero_impact_unknowns_are_never_proposed():
    lg = Ledger()
    lg.add("irrelevant", ASSUMPTION, impact=0, cost=0)
    assert lg.next_test() is None


def test_fact_requires_evidence_to_be_earned():
    lg = Ledger()
    it = lg.add("config is loaded from /etc", ASSUMPTION, impact=2)
    lg.verify(it.id, evidence="cat /etc/app.conf showed the key")
    assert lg.items[0].kind == FACT and lg.items[0].evidence
    assert lg.items[0] not in lg.open_uncertainties()


def test_unevidenced_facts_are_flagged():
    lg = Ledger()
    lg.add("definitely true", FACT)          # asserted, never checked
    assert len(lg.unevidenced_facts()) == 1
    assert "no evidence" in lg.render()


def test_refuted_assumption_is_kept_not_deleted():
    lg = Ledger()
    it = lg.add("the API returns JSON", ASSUMPTION, impact=3)
    lg.refute(it.id, evidence="returned XML")
    assert len(lg.items) == 1
    assert "REFUTED" in lg.items[0].statement


def test_bad_kind_degrades_instead_of_raising():
    lg = Ledger()
    it = lg.add("something", kind="not-a-kind")
    assert it.kind == ASSUMPTION


def test_ledger_round_trips_through_disk(tmp_path):
    p = tmp_path / "gt.json"
    lg = Ledger(p)
    lg.add("x", ASSUMPTION, impact=2)
    assert Ledger(p).next_test().statement == "x"


def test_corrupt_ledger_file_does_not_raise(tmp_path):
    p = tmp_path / "gt.json"
    p.write_text("{ not json", encoding="utf-8")
    assert Ledger(p).items == []


# ── predictions: pre-registration is the product ─────────────────────────────
def test_prediction_registered_before_observation():
    r = Register()
    p = r.register("scaffold beats retry", "rescue rate > 6%", "rate <= 6%")
    assert p.status == OPEN and not p.observed


def test_caller_supplied_verdict_beats_string_equality():
    # a numeric result inside a pre-registered band is a match even though the
    # strings differ — the DPO kill-rule case
    r = Register()
    p = r.register("v4 lands at chance", "0.50 +/- 0.03", "outside [0.47, 0.53]")
    r.resolve(p.id, "0.474", matched=True, note="inside the band")
    assert r.items[0].status == CONFIRMED


def test_refutation_is_recorded_not_silently_dropped():
    r = Register()
    p = r.register("claim", "+6.8 points", "control matches it")
    r.resolve(p.id, "+0.0 points", matched=False, note="plain retry matched")
    assert r.items[0].status == REFUTED
    assert r.calibration()["refuted"] == 1


def test_predictions_without_a_falsifier_are_flagged():
    r = Register()
    r.register("it will work", "better results")     # no falsifier = a hope
    assert len(r.unfalsifiable()) == 1
    assert "no falsifier" in r.render()


def test_calibration_counts_and_hit_rate():
    r = Register()
    a = r.register("a", "x", "not x"); b = r.register("b", "y", "not y")
    r.resolve(a.id, "x", matched=True); r.resolve(b.id, "z", matched=False)
    c = r.calibration()
    assert c["resolved"] == 2 and c["confirmed"] == 1 and c["refuted"] == 1
    assert abs(c["hit_rate"] - 0.5) < 1e-9


def test_resolving_unknown_id_returns_none_not_raises():
    assert Register().resolve("nope", "obs") is None


def test_register_round_trips_and_survives_corruption(tmp_path):
    p = tmp_path / "pred.json"
    r = Register(p); r.register("c", "e", "f")
    assert len(Register(p).items) == 1
    p.write_text("garbage", encoding="utf-8")
    assert Register(p).items == []

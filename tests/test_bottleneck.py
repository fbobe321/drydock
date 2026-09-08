"""Tests for the bottleneck register (first-principles loop, the step between decompose
and hypothesize). Advisory artifact, so the tests assert the RANKING — that share×headroom
beats both the biggest-share wall and the highest-headroom decoy — and the never-raise
contract. These two are what make it worth having over a prompt that says "find the
bottleneck first" (which was measured and lost to a plain retry)."""

from drydock.bottleneck import Bottlenecks


# ── the ranking is the product ────────────────────────────────────────────────
def test_bottleneck_is_share_times_headroom_not_biggest_share():
    b = Bottlenecks()
    # A wall: dominates the objective (80%) but cannot be moved — a fundamental constraint.
    b.add("network round-trip latency", share=0.8, headroom=0.0)
    # The real lever: smaller share but actually improvable.
    real = b.add("JSON re-parsing per row", share=0.3, headroom=0.9)
    bn = b.bottleneck()
    assert bn is not None and bn.id == real.id, \
        "must attack the movable lever, not the bigger fundamental constraint"


def test_bottleneck_is_not_the_highest_headroom_decoy():
    # the project's own lesson: a component with lots of headroom but little SHARE feels
    # productive to optimise and is capped by its share (search-quality vs throughput).
    b = Bottlenecks()
    decoy = b.add("search quality", share=0.1, headroom=1.0)   # gain 0.10
    lever = b.add("trace throughput", share=0.6, headroom=0.5)  # gain 0.30
    assert b.bottleneck().id == lever.id
    assert b.misplaced_effort() is not None
    assert b.misplaced_effort().id == decoy.id


def test_ties_break_toward_the_larger_ceiling():
    b = Bottlenecks()
    a = b.add("A", share=0.2, headroom=1.0)   # gain 0.20, ceiling 0.20
    big = b.add("B", share=0.4, headroom=0.5)  # gain 0.20, ceiling 0.40
    assert b.bottleneck().id == big.id, "equal gain → prefer the higher ceiling"
    assert a.gain() == big.gain()


def test_all_walls_yields_no_bottleneck():
    b = Bottlenecks()
    b.add("speed of light", share=0.7, headroom=0.0)
    b.add("fixed budget", share=0.3, headroom=0.02)
    assert b.bottleneck() is None
    assert "different decomposition" in b.render()
    assert len(b.walls()) == 2


def test_amdahl_ceiling_caps_a_small_share_component():
    b = Bottlenecks()
    c = b.add("a 5% stage", share=0.05, headroom=1.0)
    assert abs(c.ceiling() - 0.05) < 1e-9
    assert "ceiling 5%" in b.render()


def test_attacking_bottleneck_shifts_ranking_to_next_factor():
    b = Bottlenecks()
    first = b.add("stage one", share=0.5, headroom=0.8)   # gain 0.40
    second = b.add("stage two", share=0.4, headroom=0.6)  # gain 0.24
    assert b.bottleneck().id == first.id
    b.update(first.id, headroom=0.0, note="optimised away")  # solved it
    assert b.bottleneck().id == second.id


def test_overlapping_decomposition_is_flagged():
    b = Bottlenecks()
    b.add("x", share=0.7, headroom=0.5)
    b.add("y", share=0.6, headroom=0.5)   # shares sum to 130%
    assert ">100%" in b.render()


def test_out_of_range_numbers_are_clamped_not_raised():
    b = Bottlenecks()
    c = b.add("weird", share=5.0, headroom=-2.0)
    assert c.share == 1.0 and c.headroom == 0.0


def test_non_numeric_numbers_degrade_instead_of_raising():
    b = Bottlenecks()
    c = b.add("bad", share="lots", headroom=None)  # type: ignore[arg-type]
    assert c.share == 0.0 and c.headroom == 1.0    # defaults, no exception


def test_update_unknown_id_returns_none_not_raises():
    assert Bottlenecks().update("nope", share=0.5) is None


def test_round_trips_through_disk(tmp_path):
    p = tmp_path / "bn.json"
    b = Bottlenecks(p)
    b.add("stage", share=0.6, headroom=0.5)
    assert Bottlenecks(p).bottleneck().name == "stage"


def test_corrupt_file_does_not_raise(tmp_path):
    p = tmp_path / "bn.json"
    p.write_text("{ not json", encoding="utf-8")
    assert Bottlenecks(p).items == []


def test_empty_register_renders_without_raising():
    assert "no components" in Bottlenecks().render()

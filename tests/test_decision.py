"""Tests for the Laya System-1 Decision Plane (docs/laya_prd.md).

Pure logic + injected transport: no live Laya server. These pin the interface contract, the
heuristic fallback's behaviour, the confidence router, the fallback chain, decision logging,
and the ABC coupling (§11).
"""
from __future__ import annotations

import json

from drydock.decision import (
    EXECUTE_THRESHOLD,
    FALLBACK_THRESHOLD,
    RESOURCE_TO_AXIS,
    ControlState,
    Decision,
    DecisionTrace,
    DecisionUnavailable,
    FallbackDecisionProvider,
    HeuristicProvider,
    LayaProvider,
    StrategyLedger,
    batch_decisions,
    decide_context_module,
    decide_continue,
    decide_evict_module,
    decide_limiting_resource,
    decide_next_action,
    decide_reasoning,
    decision_provider,
    strategy_family,
)


# --- ControlState (§7/§8) --------------------------------------------------------------
def test_control_state_prompt_is_compact():
    s = ControlState(objective="fix concurrency bug", fitness=37, maximum=42,
                     previous_fitness=37, plateau_iterations=3,
                     active_modules=["queue", "tests"], available_modules=["queue", "tests", "db"])
    p = s.to_prompt()
    assert "37/42" in p and "plateau 3" in p
    # compact: the whole dashboard should be small (§8 target 200-500 tokens; chars << that).
    assert len(p) < 600


def test_control_state_derived_flags():
    assert ControlState(fitness=5, previous_fitness=3).improved
    assert ControlState(fitness=10, maximum=10).solved
    assert ControlState(plateau_iterations=2, fitness=3, previous_fitness=3).stalled
    assert not ControlState(plateau_iterations=2, fitness=4, previous_fitness=3).stalled


# --- Decision result (§16) -------------------------------------------------------------
def test_decision_from_scores_picks_top():
    d = Decision.from_scores("q", {"a": 0.2, "b": 0.7, "c": 0.1})
    assert d.selected == "b" and d.confidence == 0.7


def test_decision_confidence_bands():
    assert Decision.from_scores("q", {"a": 0.9, "b": 0.1}).actionable
    assert Decision.from_scores("q", {"a": 0.4, "b": 0.6}).uncertain is False
    assert Decision.from_scores("q", {"a": 0.5, "b": 0.5}).uncertain
    assert EXECUTE_THRESHOLD > FALLBACK_THRESHOLD


# --- HeuristicProvider (§18) -----------------------------------------------------------
def test_heuristic_continue_drops_with_plateau():
    h = HeuristicProvider()
    fresh = h.boolean(ControlState(fitness=5, previous_fitness=3), "Continue?")
    stuck = h.boolean(ControlState(fitness=3, previous_fitness=3, plateau_iterations=3), "Continue?")
    assert fresh.selected == "yes"
    assert stuck.options["no"] > stuck.options["yes"]


def test_heuristic_finish_requires_solved():
    h = HeuristicProvider()
    assert h.boolean(ControlState(fitness=10, maximum=10), "Is the task complete?").selected == "yes"
    assert h.boolean(ControlState(fitness=8, maximum=10), "Is the task complete?").selected == "no"


def test_heuristic_missing_context_wants_context_not_reasoning():
    h = HeuristicProvider()
    s = ControlState(fitness=3, previous_fitness=3, plateau_iterations=2,
                     active_modules=["a"], available_modules=["a", "b"])  # b unloaded
    d = h.choose(s, "What resource is most limiting progress?",
                 list(RESOURCE_TO_AXIS.keys()))
    assert d.selected == "more_context"


def test_heuristic_repeated_strategy_wants_change():
    h = HeuristicProvider()
    s = ControlState(fitness=3, previous_fitness=3, plateau_iterations=3, family_attempts=3)
    d = h.choose(s, "What resource is most limiting progress?", list(RESOURCE_TO_AXIS.keys()))
    assert d.selected == "different_strategy"


def test_heuristic_next_action_finishes_when_solved():
    h = HeuristicProvider()
    d = decide_next_action(h, ControlState(fitness=10, maximum=10,
                                           available_actions=["continue", "finish"]))
    assert d.selected == "finish"


def test_heuristic_reasoning_scales_with_plateau_never_maxed():
    h = HeuristicProvider()
    shallow = decide_reasoning(h, ControlState(plateau_iterations=0))
    deep = decide_reasoning(h, ControlState(plateau_iterations=3))
    order = ["minimal", "low", "medium", "high", "maximum"]
    assert order.index(deep.selected) >= order.index(shallow.selected)
    assert deep.selected != "maximum"          # caps at high by default


# --- LayaProvider (§5) with injected transport -----------------------------------------
def _transport_returning(options: dict):
    def t(url, payload, timeout):
        return json.dumps({"options": options})
    return t


def test_laya_parses_options():
    laya = LayaProvider(transport=_transport_returning({"yes": 0.8, "no": 0.2}))
    d = laya.boolean(ControlState(), "Continue?")
    assert d.selected == "yes" and d.source == "laya"


def test_laya_unavailable_on_transport_error():
    def boom(url, payload, timeout):
        raise ConnectionError("refused")
    laya = LayaProvider(transport=boom)
    try:
        laya.boolean(ControlState(), "Continue?")
        assert False, "expected DecisionUnavailable"
    except DecisionUnavailable:
        pass


def test_laya_unavailable_on_empty_options():
    laya = LayaProvider(transport=_transport_returning({}))
    try:
        laya.choose(ControlState(), "q", ["a", "b"])
        assert False, "expected DecisionUnavailable"
    except DecisionUnavailable:
        pass


# --- Fallback chain (§18) --------------------------------------------------------------
def test_fallback_uses_heuristic_when_laya_down():
    def boom(url, payload, timeout):
        raise TimeoutError()
    chain = FallbackDecisionProvider([LayaProvider(transport=boom), HeuristicProvider()])
    d = chain.boolean(ControlState(fitness=5, previous_fitness=3), "Continue?")
    assert d.source == "heuristic" and d.selected == "yes"


def test_fallback_prefers_confident_primary():
    laya = LayaProvider(transport=_transport_returning({"yes": 0.95, "no": 0.05}))
    chain = FallbackDecisionProvider([laya, HeuristicProvider()])
    d = chain.boolean(ControlState(), "Continue?")
    assert d.source == "laya"


def test_fallback_moves_past_uncertain_primary():
    laya = LayaProvider(transport=_transport_returning({"yes": 0.5, "no": 0.5}))  # uncertain
    chain = FallbackDecisionProvider([laya, HeuristicProvider()])
    d = chain.boolean(ControlState(fitness=5, previous_fitness=3), "Continue?")
    assert d.source == "heuristic"


# --- factory (§19) ---------------------------------------------------------------------
def test_factory_default_is_heuristic():
    assert decision_provider({}).name == "heuristic"
    assert decision_provider({"decision_plane": {"enabled": False}}).name == "heuristic"


def test_factory_reads_real_default_config():
    # the shipped DEFAULTS must resolve to the heuristic (decision plane off by default).
    from drydock.config import DEFAULTS
    assert decision_provider(DEFAULTS).name == "heuristic"


def test_factory_laya_wrapped_in_fallback():
    dp = decision_provider(
        {"decision_plane": {"enabled": True, "provider": "laya"}},
        laya_transport=_transport_returning({"yes": 0.9, "no": 0.1}),
    )
    assert dp.name == "fallback"
    assert dp.boolean(ControlState(), "Continue?").source == "laya"


# --- decision trace (§20) --------------------------------------------------------------
def test_trace_logs_to_disk(tmp_path):
    trace = DecisionTrace(tmp_path / "d.jsonl")
    h = HeuristicProvider()
    d = decide_continue(h, ControlState(fitness=5, previous_fitness=3))
    trace.log(ControlState(objective="x"), d, outcome={"fitness_after": 7})
    line = (tmp_path / "d.jsonl").read_text().strip()
    rec = json.loads(line)
    assert rec["source"] == "heuristic" and rec["outcome"]["fitness_after"] == 7


def test_trace_never_raises_on_bad_path():
    trace = DecisionTrace("/nonexistent-root/cannot/create/d.jsonl")
    # must not raise even when the path is unwritable.
    trace.log(ControlState(), Decision(question="q"))


# --- ABC coupling (§11) ----------------------------------------------------------------
def test_resource_axis_map_covers_all_options():
    for r in RESOURCE_TO_AXIS:
        assert r in ("more_reasoning", "more_context", "more_agents", "more_tools",
                     "different_strategy", "none")


def test_limiting_resource_decision_maps_to_axis():
    h = HeuristicProvider()
    s = ControlState(fitness=3, previous_fitness=3, plateau_iterations=2,
                     active_modules=["a"], available_modules=["a", "b"])
    d = decide_limiting_resource(h, s)
    assert RESOURCE_TO_AXIS[d.selected] == "context"


# --- state hashing (§20) ---------------------------------------------------------------
def test_state_hash_stable_and_discriminating():
    a = ControlState(objective="x", fitness=3, plateau_iterations=2)
    b = ControlState(objective="x", fitness=3, plateau_iterations=2)
    c = ControlState(objective="x", fitness=4, plateau_iterations=2)
    assert a.state_hash() == b.state_hash()
    assert a.state_hash() != c.state_hash()


def test_trace_records_state_hash(tmp_path):
    trace = DecisionTrace(tmp_path / "d.jsonl")
    s = ControlState(objective="x", fitness=3)
    rec = trace.log(s, Decision(question="q"))
    assert rec["state_hash"] == s.state_hash()


# --- context routing (§12/§13) ---------------------------------------------------------
def test_context_module_choice_offers_unloaded_only():
    h = HeuristicProvider()
    s = ControlState(active_modules=["base", "auth"], available_modules=["base", "auth", "db", "api"])
    d = decide_context_module(h, s)
    assert set(d.options) <= {"db", "api", "none"}
    assert "auth" not in d.options


def test_context_module_choice_empty_when_all_loaded():
    h = HeuristicProvider()
    s = ControlState(active_modules=["base", "db"], available_modules=["base", "db"])
    assert decide_context_module(h, s).source == "none"


def test_evict_never_offers_base():
    h = HeuristicProvider()
    s = ControlState(active_modules=["base", "auth", "db"])
    d = decide_evict_module(h, s)
    assert "base" not in d.options


# --- batched decisions (§10) -----------------------------------------------------------
def test_batch_answers_all_questions():
    h = HeuristicProvider()
    s = ControlState(fitness=5, previous_fitness=3, available_actions=["continue", "finish"])
    out = batch_decisions(h, s, {
        "continue": ("bool", "Continue?", []),
        "next": ("choose", "What next?", ["continue", "finish"]),
        "reasoning": ("choose", "How much reasoning?", ["low", "medium", "high"]),
    })
    assert set(out) == {"continue", "next", "reasoning"}
    assert out["continue"].selected in ("yes", "no")


def test_batch_degrades_on_unavailable():
    def boom(url, payload, timeout):
        raise ConnectionError()
    laya = LayaProvider(transport=boom)
    out = batch_decisions(laya, ControlState(), {"c": ("bool", "Continue?", [])})
    assert out["c"].source == "none"      # abstention, not an exception


# --- strategy families (§15) -----------------------------------------------------------
def test_strategy_family_folds_synonyms():
    # the PRD's own example: these should collapse to the same family.
    assert strategy_family("increase mutex scope") == strategy_family("lock larger section")


def test_strategy_family_distinguishes_real_differences():
    assert strategy_family("rewrite the parser") != strategy_family("add a database index")


def test_strategy_ledger_invalidates_after_flat_attempts():
    led = StrategyLedger(invalidate_after=3)
    # three lexically-different phrasings of the SAME family (expand the lock scope).
    led.record("increase mutex scope", fitness_gain=0)
    led.record("lock larger section", fitness_gain=0)
    key = led.record("expand the locked region", fitness_gain=0)
    assert led.is_invalidated("widen the lock scope")   # same family, unseen phrasing
    assert any(key in lesson for lesson in led.lessons())


def test_strategy_ledger_progress_prevents_invalidation():
    led = StrategyLedger(invalidate_after=3)
    for _ in range(3):
        led.record("expand lock scope", fitness_gain=2)   # gaining each time
    assert not led.is_invalidated("expand lock scope")
    assert led.lessons() == []

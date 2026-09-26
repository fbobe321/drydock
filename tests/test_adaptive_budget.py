"""Tests for the Adaptive Budget Controller (docs/abc_prd.md §20 MVP).

Pure logic, no live model: these pin the control policy — cheap start, targeted escalation on
sustained plateau, de-escalation when progress resumes, and an honest terminate when the ladder
is exhausted.
"""
from __future__ import annotations

from drydock.adaptive_budget import (
    LEVELS,
    PLATEAU_TO_ESCALATE,
    AdaptiveBudgetController,
    RatchetBudgetAdvisor,
    ReasoningBudget,
    ResourceEnvelope,
    TaskProbe,
    probe_to_envelope,
)


# --- probe -> initial envelope (§4/§5) --------------------------------------------------
def test_trivial_probe_yields_cheap_envelope():
    env = probe_to_envelope(TaskProbe(complexity="minimal"))
    assert env.reasoning.level == "minimal"
    assert env.agents == 1
    assert env.context_modules == 2
    assert env.tool_calls == 10


def test_probe_scales_context_with_files_and_tools():
    env = probe_to_envelope(TaskProbe(files_implicated=4, tool_dependency="high"))
    assert env.context_modules == 5           # 2 + (4-1)
    assert env.context_tokens == 8000 * 5
    assert env.tool_calls == 20               # high tool dependency


def test_probe_context_modules_capped():
    env = probe_to_envelope(TaskProbe(files_implicated=50))
    assert env.context_modules == 6           # capped


def test_multiple_hypotheses_start_parallel_bounded_by_concurrency():
    env = probe_to_envelope(TaskProbe(hypotheses=8), concurrency=3)
    # swarm_size clamps to server concurrency, so never more than 3 agents here.
    assert 1 < env.agents <= 3


def test_reasoning_budget_presets_ordered():
    tokens = [ReasoningBudget.at(l).token_budget for l in LEVELS]
    assert tokens == sorted(tokens)           # minimal < low < medium < high


# --- allocation wires the tool budget --------------------------------------------------
def test_allocate_sets_tool_budget_state():
    c = AdaptiveBudgetController()
    c.allocate(TaskProbe(tool_dependency="high"))
    assert c.budget.max_tool_calls == c.envelope.tool_calls == 20


# --- plateau -> escalation (§7, §20 item 3) --------------------------------------------
def test_two_plateaus_escalate_reasoning_one_level():
    c = AdaptiveBudgetController()
    c.allocate(TaskProbe(complexity="low"))
    assert c.envelope.reasoning.level == "low"

    d1 = c.observe(passed=5, total=10)        # first datapoint: no prior score -> hold
    assert d1.action == "hold"
    d2 = c.observe(passed=5, total=10)        # plateau 1 -> still watching
    assert d2.action == "hold"
    d3 = c.observe(passed=5, total=10)        # plateau 2 -> escalate
    assert d3.action == "escalate"
    assert d3.axis == "reasoning"
    assert c.envelope.reasoning.level == "medium"
    assert c.escalations == 1


def test_progress_resets_plateau_streak():
    c = AdaptiveBudgetController()
    c.allocate(TaskProbe(complexity="low"))
    c.observe(passed=5, total=10)
    c.observe(passed=5, total=10)             # plateau 1
    prog = c.observe(passed=7, total=10)      # progress -> reset, no escalation
    assert prog.action == "hold"
    assert c.escalations == 0
    # streak reset: needs PLATEAU_TO_ESCALATE fresh plateaus again
    c.observe(passed=7, total=10)
    d = c.observe(passed=7, total=10)
    assert d.action == "escalate"


# --- targeted ladder: reasoning -> context -> tools -> agents (§8/§9) -------------------
def _plateau_until_escalate(c, score):
    """Drive `c` to its next escalation and return that decision."""
    for _ in range(PLATEAU_TO_ESCALATE + 1):
        d = c.observe(passed=score, total=100)
        if d.action in ("escalate", "terminate"):
            return d
    raise AssertionError("expected an escalation")


def test_ladder_walks_axes_in_order():
    c = AdaptiveBudgetController(concurrency=8)
    c.allocate(TaskProbe(complexity="low", files_implicated=1))
    axes = []
    for _ in range(6):
        d = _plateau_until_escalate(c, 5)
        axes.append((d.action, d.axis))
        if d.terminate:
            break
    actions = [a for a, _ in axes]
    escalated_axes = [ax for a, ax in axes if a == "escalate"]
    assert escalated_axes[:4] == ["reasoning", "context", "tools", "agents"]
    assert actions[-1] == "terminate"         # ladder eventually exhausts -> honest stop


def test_context_escalation_adds_module_and_tokens():
    c = AdaptiveBudgetController()
    c.allocate(TaskProbe(complexity="high"))  # reasoning already HIGH -> skips reasoning rung
    before_mods = c.envelope.context_modules
    d = _plateau_until_escalate(c, 3)
    assert d.axis == "context"                # reasoning maxed, so first real raise is context
    assert c.envelope.context_modules == before_mods + 1


def test_tool_escalation_updates_budget_state():
    c = AdaptiveBudgetController()
    c.allocate(TaskProbe(complexity="high", files_implicated=8))  # reasoning+context near max
    # walk to the tools rung
    seen = set()
    for _ in range(6):
        d = _plateau_until_escalate(c, 1)
        seen.add(d.axis)
        if d.axis == "tools":
            assert c.budget.max_tool_calls == c.envelope.tool_calls
            break
        if d.terminate:
            break
    assert "tools" in seen


# --- de-escalation (§15) ---------------------------------------------------------------
def test_progress_after_escalation_deescalates():
    c = AdaptiveBudgetController()
    c.allocate(TaskProbe(complexity="low"))
    _plateau_until_escalate(c, 5)             # reasoning low -> medium
    assert c.envelope.reasoning.level == "medium"
    d = c.observe(passed=9, total=10)         # verified progress -> release budget
    assert d.action == "deescalate"
    assert c.envelope.reasoning.level == "low"
    assert c.deescalations == 1


def test_deescalation_never_drops_below_initial_floor():
    c = AdaptiveBudgetController()
    c.allocate(TaskProbe(complexity="medium"))
    floor = c.envelope.reasoning.level
    # progress from the start should never lower reasoning below the initial floor.
    c.observe(passed=1, total=10)
    d = c.observe(passed=5, total=10)
    assert d.action == "hold"                 # never escalated -> nothing to release
    assert c.envelope.reasoning.level == floor


def test_regression_does_not_deescalate():
    c = AdaptiveBudgetController()
    c.allocate(TaskProbe(complexity="low"))
    _plateau_until_escalate(c, 5)             # -> medium
    d = c.observe(passed=2, total=10)         # score dropped: a regression, not progress
    assert d.action == "hold"
    assert c.envelope.reasoning.level == "medium"   # budget held, not released


# --- ledger (§16) ----------------------------------------------------------------------
def test_ledger_records_envelope_and_history():
    c = AdaptiveBudgetController()
    c.allocate(TaskProbe(complexity="low"))
    _plateau_until_escalate(c, 5)
    led = c.ledger()
    assert led["escalations"] == 1
    assert led["envelope"]["reasoning"]["level"] == "medium"
    assert led["initial"]["reasoning"]["level"] == "low"
    assert any(d["action"] == "escalate" for d in led["decisions"])


# --- robustness ------------------------------------------------------------------------
def test_controller_never_raises_on_unknown_level():
    env = ResourceEnvelope(reasoning=ReasoningBudget(level="bogus"))
    c = AdaptiveBudgetController()
    c.envelope = env
    # an unknown level must not crash escalation; it is treated as "low".
    d = _plateau_until_escalate(c, 0)
    assert d.action in ("escalate", "terminate")


# --- ratchet advisor bridge (§14) ------------------------------------------------------
def test_advisor_stays_quiet_while_progressing():
    adv = RatchetBudgetAdvisor(effort="low")
    assert adv.observe_round(2, 10) is None    # baseline
    assert adv.observe_round(4, 10) is None     # progress -> no note
    assert adv.observe_round(6, 10) is None     # progress -> no note


def test_advisor_notes_escalation_on_sustained_plateau():
    adv = RatchetBudgetAdvisor(effort="low")
    adv.observe_round(3, 10)                     # baseline
    assert adv.observe_round(3, 10) is None      # plateau 1 -> quiet
    note = adv.observe_round(3, 10)              # plateau 2 -> escalate
    assert note is not None
    assert "budget" in note and "reasoning=medium" in note


def test_advisor_notes_release_on_resumed_progress():
    adv = RatchetBudgetAdvisor(effort="low")
    adv.observe_round(3, 10)
    adv.observe_round(3, 10)
    adv.observe_round(3, 10)                     # escalated to medium
    note = adv.observe_round(8, 10)             # progress -> release
    assert note is not None and "releasing" in note


def test_advisor_effort_seeds_initial_level():
    adv = RatchetBudgetAdvisor(effort="high")
    assert adv.controller.envelope.reasoning.level == "high"


def test_advisor_ledger_exposed():
    adv = RatchetBudgetAdvisor(effort="medium")
    adv.observe_round(1, 10)
    led = adv.ledger()
    assert "envelope" in led and "decisions" in led


def test_advisor_never_raises_on_garbage_input():
    adv = RatchetBudgetAdvisor(effort="nonsense")
    # unknown effort falls back to a sane level, and bad scores must not raise.
    assert adv.controller.envelope.reasoning.level in LEVELS
    assert adv.observe_round(0, 0) is None

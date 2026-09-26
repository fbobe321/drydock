"""Adaptive inference budgeting — the Handshake Adaptive Budget Controller (ABC).

Drydock treats inference compute as a resource to ALLOCATE, not a fixed property of a run.
Instead of predicting a task's cost up front and applying a static reasoning/context/agent/
tool budget, ABC starts cheap, watches the verified progress signal the loop already
produces, and escalates resources only when justified — and, just as important, DE-escalates
them once discovery makes a hard task easy (docs/abc_prd.md §15). "Failure != think harder":
the controller picks WHICH axis to raise (reasoning / context / agents / tools), per §8.

This is the MVP scheduler (§20). It is deliberately provider-independent and pure logic — no
live model, no network — so the central hypothesis (adaptive ~= max-compute at less compute)
can be tested in isolation before the backend adapter and TUI wiring land. It COMPOSES the
existing substrate rather than reimplementing it:

  * agent sizing   -> capacity.swarm_size (§3 agent budget)
  * tool ceiling   -> budget.BudgetState (§3 tool budget, §16 ledger)
  * plateau signal -> progress.ProgressTracker semantics (§7, §10)

The one genuinely new axis is resource escalation itself: today the plateau streak only drives
prompt-firmness nudges (recovery.py); ABC makes the same streak reallocate the four budgets.

All logic original to Drydock. Never raises for control-flow reasons; a controller that blows
up mid-run is worse than one that holds the current budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .budget import BudgetState
from .capacity import DEFAULT_TASK_DEMAND, swarm_size

# --- reasoning levels (§6): presets, not hard boundaries. The controller advances/retreats
#     along this ordered list; the backend adapter (not yet built) translates a level into
#     whatever the local fleet supports (llama.cpp --reasoning-budget, vLLM, or, for a model
#     with no explicit control, an iteration/generation approximation). NEVER a hosted API.
LEVELS: tuple[str, ...] = ("minimal", "low", "medium", "high")

# token budget + loop-iteration depth per level. Approximations; tuned once wired to a fleet.
_LEVEL_TOKENS = {"minimal": 2000, "low": 4000, "medium": 12000, "high": 32000}
_LEVEL_ITERS = {"minimal": 2, "low": 4, "medium": 8, "high": 16}

# how many CONSECUTIVE plateau observations trip one rung of escalation (§20 item 3).
PLATEAU_TO_ESCALATE = 2

# --- backend adapter (§5): translate a provider-independent level into the one request knob
#     Drydock's provider layer already forwards (providers.py: config["reasoning_effort"] ->
#     kwargs, ignored by endpoints without the knob). ABC's own "minimal" collapses to the
#     provider's "low"; the loop's iteration budget (envelope.max_iterations) is the
#     approximation for models with no reasoning knob at all — so the adapter never touches
#     max_tokens (which would cap the ANSWER, not just the reasoning).
_LEVEL_TO_EFFORT = {"minimal": "low", "low": "low", "medium": "medium", "high": "high"}


def reasoning_turn_config(reasoning: "ReasoningBudget") -> dict:
    """Map a ReasoningBudget onto the turn-config keys Drydock's providers understand.

    Returns only `reasoning_effort`. agent.py honors a pre-set reasoning_effort (it sets its
    own only `if "reasoning_effort" not in turn_config`), so a caller that merges this into a
    turn config actuates ABC's choice without fighting the per-turn effort governor — which
    remains free to force LOW later (the §17 safety hierarchy: policy limits outrank ABC)."""
    level = getattr(reasoning, "level", "low")
    return {"reasoning_effort": _LEVEL_TO_EFFORT.get(level, "low")}


def _level_index(level: str) -> int:
    try:
        return LEVELS.index(level)
    except ValueError:
        return 1  # unknown -> treat as "low"


@dataclass
class ReasoningBudget:
    """Provider-independent reasoning allocation (§5). The adapter maps `level` onto the
    backend; `token_budget` / `iterations` are the fallbacks for a model with no explicit
    reasoning control."""
    level: str = "low"
    token_budget: int = 4000
    iterations: int = 4

    @classmethod
    def at(cls, level: str) -> "ReasoningBudget":
        level = level if level in _LEVEL_TOKENS else "low"
        return cls(level=level, token_budget=_LEVEL_TOKENS[level], iterations=_LEVEL_ITERS[level])

    def to_dict(self) -> dict:
        return {"level": self.level, "token_budget": self.token_budget, "iterations": self.iterations}


@dataclass
class ResourceEnvelope:
    """The four budgets under control (§3), plus the execution ceiling (§5)."""
    reasoning: ReasoningBudget = field(default_factory=ReasoningBudget)
    context_modules: int = 2
    context_tokens: int = 16000
    agents: int = 1
    tool_calls: int = 10
    max_iterations: int = 4

    def to_dict(self) -> dict:
        return {
            "reasoning": self.reasoning.to_dict(),
            "context_modules": self.context_modules,
            "context_tokens": self.context_tokens,
            "agents": self.agents,
            "tool_calls": self.tool_calls,
            "max_iterations": self.max_iterations,
        }


@dataclass
class TaskProbe:
    """Cheap classification of a request (§4) — measurable characteristics preferred over
    model intuition. The Handshake probe fills this; the controller maps it to an initial
    envelope. All fields optional so an under-informed probe still yields a sane envelope."""
    complexity: str = "low"          # minimal | low | medium | high
    ambiguity: str = "low"           # low | high  -> agents when high
    tool_dependency: str = "low"     # low | high  -> tool_calls when high
    files_implicated: int = 1
    tests_available: bool = False
    hypotheses: int = 1              # independent approaches -> parallelizable
    parallelizable: bool = False
    long_horizon_risk: str = "low"   # low | high


def probe_to_envelope(probe: TaskProbe, *, concurrency: int = 4) -> ResourceEnvelope:
    """Turn a probe into the CHEAPEST plausible starting envelope (§2, §5). Deliberately
    conservative: the loop escalates from here when progress stalls, so starting small is the
    whole point. `concurrency` bounds any agent fan-out to what the server actually serves."""
    level = probe.complexity if probe.complexity in _LEVEL_TOKENS else "low"
    reasoning = ReasoningBudget.at(level)

    # context scales with how much of the repo the task touches (§4 measurable signal).
    modules = 2 + max(0, probe.files_implicated - 1)
    modules = min(modules, 6)
    context_tokens = 8000 * modules

    # start single-agent unless the probe already knows several hypotheses exist (§8 agent).
    agents = 1
    if probe.parallelizable or probe.hypotheses > 1:
        agents = swarm_size(concurrency, task_demand=max(2, probe.hypotheses))

    tool_calls = 20 if probe.tool_dependency == "high" else 10
    return ResourceEnvelope(
        reasoning=reasoning,
        context_modules=modules,
        context_tokens=context_tokens,
        agents=agents,
        tool_calls=tool_calls,
        max_iterations=reasoning.iterations,
    )


@dataclass
class BudgetDecision:
    """What the controller did on one observation, for the caller and the ledger."""
    action: str                       # hold | escalate | deescalate | terminate
    axis: str = ""                    # reasoning | context | tools | agents | long_horizon
    reason: str = ""
    envelope: ResourceEnvelope = field(default_factory=ResourceEnvelope)
    terminate: bool = False

    def to_dict(self) -> dict:
        return {"action": self.action, "axis": self.axis, "reason": self.reason,
                "terminate": self.terminate}


class AdaptiveBudgetController:
    """Closed-loop inference controller (§15). `probe`/`allocate` set the initial envelope;
    `observe` feeds each round's verified score and returns the (possibly changed) envelope.

    Escalation is TARGETED and walks the §9 ladder one rung per sustained plateau:
        reasoning -> context -> tools -> agents (fan-out) -> long-horizon -> terminate.
    De-escalation (§15) fires when real progress resumes after the budget was raised, so a
    hard task that becomes easy after discovery drops back toward cheap. The controller never
    downgrades below the initial envelope on its own.
    """

    # the ordered escalation ladder (§9); "long_horizon" is the last rung before termination.
    _LADDER = ("reasoning", "context", "tools", "agents", "long_horizon")

    def __init__(self, *, concurrency: int = 4) -> None:
        self.concurrency = max(1, concurrency)
        self.envelope = ResourceEnvelope()
        self._initial = ResourceEnvelope()
        self.budget = BudgetState()
        self.long_horizon = False

        self._last_score: int | None = None   # verified progress score of the previous observe
        self._plateau_streak = 0               # consecutive observations without progress
        self._rung = 0                         # position on _LADDER
        self._escalated = False                # have we ever raised above the initial envelope?
        self.decisions: list[BudgetDecision] = []
        self.escalations = 0
        self.deescalations = 0

    # -- setup -------------------------------------------------------------------------
    def allocate(self, probe: TaskProbe) -> ResourceEnvelope:
        """Set the initial envelope from a probe (§5). Records it as the floor for later
        de-escalation."""
        self.envelope = probe_to_envelope(probe, concurrency=self.concurrency)
        self._initial = replace(
            self.envelope,
            reasoning=replace(self.envelope.reasoning),
        )
        self.budget.max_tool_calls = self.envelope.tool_calls
        return self.envelope

    # -- feedback loop -----------------------------------------------------------------
    def observe(self, *, passed: int, total: int) -> BudgetDecision:
        """Record one round's verifier result (§7/§10). Verified progress = `passed` rose
        since the last observation. Progress resets the plateau streak and may de-escalate;
        a sustained plateau (PLATEAU_TO_ESCALATE in a row) escalates one ladder rung."""
        score = int(passed)
        if self._last_score is None:
            # baseline round: nothing to compare against yet, so it is neither progress nor
            # plateau. Establish the reference and hold without touching the streak.
            self._last_score = score
            return self._hold("baseline observation; holding budget")

        made_progress = score > self._last_score
        regressed = score < self._last_score
        self._last_score = score

        if made_progress:
            self._plateau_streak = 0
            if self._escalated:
                return self._deescalate("verified progress resumed; releasing raised budget")
            return self._hold("progress; holding budget")

        # no forward progress this round.
        self._plateau_streak += 1
        # a regression is a stronger signal than a flat plateau — treat it as a plateau tick
        # but never let it de-escalate.
        if self._plateau_streak < PLATEAU_TO_ESCALATE:
            reason = "regression noted; watching" if regressed else "plateau; watching"
            return self._hold(reason)

        # sustained plateau -> escalate one rung, then reset the streak so each rung needs a
        # fresh sustained plateau to advance (§20 item 3/4).
        self._plateau_streak = 0
        return self._escalate()

    # -- escalation --------------------------------------------------------------------
    def _escalate(self) -> BudgetDecision:
        while self._rung < len(self._LADDER):
            axis = self._LADDER[self._rung]
            changed, reason = self._apply_escalation(axis)
            if changed:
                self._rung += 1
                self._escalated = True
                self.escalations += 1
                return self._record("escalate", axis, reason)
            # axis already maxed (e.g. reasoning already HIGH) -> try the next rung.
            self._rung += 1
        # ladder exhausted: nothing left to raise -> honest stop (§9 L6 -> terminate).
        return self._record("terminate", "long_horizon",
                            "escalation ladder exhausted; no resource left to raise",
                            terminate=True)

    def _apply_escalation(self, axis: str) -> tuple[bool, str]:
        env = self.envelope
        if axis == "reasoning":
            idx = _level_index(env.reasoning.level)
            if idx >= len(LEVELS) - 1:
                return False, ""
            new = ReasoningBudget.at(LEVELS[idx + 1])
            env.reasoning = new
            env.max_iterations = new.iterations
            return True, f"reasoning {LEVELS[idx]} -> {new.level}"
        if axis == "context":
            if env.context_modules >= 8:
                return False, ""
            env.context_modules += 1
            env.context_tokens += 8000
            return True, f"context -> {env.context_modules} modules"
        if axis == "tools":
            env.tool_calls += 10
            self.budget.max_tool_calls = env.tool_calls
            return True, f"tool budget -> {env.tool_calls} calls"
        if axis == "agents":
            new_agents = swarm_size(
                self.concurrency,
                task_demand=max(env.agents + 2, DEFAULT_TASK_DEMAND),
                budget_agents=None,
            )
            if new_agents <= env.agents:
                return False, ""
            env.agents = new_agents
            return True, f"agent fan-out -> {new_agents}"
        if axis == "long_horizon":
            if self.long_horizon:
                return False, ""
            self.long_horizon = True
            return True, "long-horizon / ratchet mode engaged"
        return False, ""

    # -- de-escalation (§15) -----------------------------------------------------------
    def _deescalate(self, reason: str) -> BudgetDecision:
        env = self.envelope
        # drop reasoning one level (never below the initial floor) and step agents back toward
        # the floor. Context stays: modules loaded during discovery are now known-useful.
        floor = self._initial
        idx = _level_index(env.reasoning.level)
        floor_idx = _level_index(floor.reasoning.level)
        if idx > floor_idx:
            env.reasoning = ReasoningBudget.at(LEVELS[idx - 1])
            env.max_iterations = env.reasoning.iterations
        if env.agents > floor.agents:
            env.agents = max(floor.agents, env.agents - 1)
        # long_horizon, once engaged, stays on: dropping ratchet mode mid-run would discard
        # accumulated checkpoints. De-escalation releases compute, not the safety net.
        self._rung = max(0, self._rung - 1)
        if self._rung == 0:
            self._escalated = False
        self.deescalations += 1
        return self._record("deescalate", "reasoning", reason)

    # -- helpers -----------------------------------------------------------------------
    def _hold(self, reason: str) -> BudgetDecision:
        return self._record("hold", "", reason)

    def _record(self, action: str, axis: str, reason: str, *, terminate: bool = False) -> BudgetDecision:
        d = BudgetDecision(
            action=action,
            axis=axis,
            reason=reason,
            envelope=replace(self.envelope, reasoning=replace(self.envelope.reasoning)),
            terminate=terminate,
        )
        self.decisions.append(d)
        return d

    # -- actuation (§5 backend adapter) ------------------------------------------------
    @property
    def reasoning_effort(self) -> str:
        """The current reasoning level as the provider's request label."""
        return reasoning_turn_config(self.envelope.reasoning)["reasoning_effort"]

    def turn_config(self) -> dict:
        """Turn-config fragment to merge into the next worker turn so the model actually
        runs at ABC's current reasoning level (§5). Merge as `{**cfg, **abc.turn_config()}`
        BEFORE the agent loop's per-turn default, since agent.py leaves a pre-set
        reasoning_effort untouched."""
        return reasoning_turn_config(self.envelope.reasoning)

    # -- ledger (§16) ------------------------------------------------------------------
    def ledger(self) -> dict:
        """What the controller allocated and how it moved — the §16 budget ledger, extended
        with the ABC-specific escalation history."""
        return {
            "envelope": self.envelope.to_dict(),
            "initial": self._initial.to_dict(),
            "escalations": self.escalations,
            "deescalations": self.deescalations,
            "long_horizon": self.long_horizon,
            "budget": self.budget.to_dict(),
            "decisions": [d.to_dict() for d in self.decisions],
        }


# ======================================================================================
# Ratchet bridge (§14) — advise on resource allocation from the /ratchet round signal.
# ======================================================================================

# what each escalation axis recommends the operator (or a future actuator) do. Until the
# backend adapter lands, ABC is ADVISORY in the ratchet loop — it names the resource to raise
# rather than raising it, per the "safety mechanisms advisory, never blocking" rule.
_ADVICE = {
    "reasoning": "raise reasoning effort — the model has evidence but keeps reaching a weak conclusion",
    "context": "add a context module — the model repeatedly lacks information",
    "tools": "spend a tool call, not more tokens — resolve the uncertainty experimentally",
    "agents": "fan out to parallel hypotheses — one line of attack has plateaued",
    "long_horizon": "engage long-horizon / ratchet mode — this needs sustained iteration",
}


def _effort_to_probe(effort: str, *, has_verifier: bool = True) -> TaskProbe:
    """Map a /ratchet effort dial onto an initial probe (§4). The ratchet knows little about
    the task up front, so this is deliberately coarse; the loop escalates from here."""
    level = effort if effort in _LEVEL_TOKENS else "low"
    return TaskProbe(
        complexity=level,
        tests_available=has_verifier,
        tool_dependency="high" if has_verifier else "low",
        long_horizon_risk="high" if level == "high" else "low",
    )


class RatchetBudgetAdvisor:
    """Thin, never-raises bridge between the /ratchet round loop and the controller (§14).

    Built once per run from the effort dial; fed each round's HONEST recorded score (the
    holdout-adjusted one). Returns a short operator-facing note only when the allocation
    should change — a plateau that trips an escalation rung, resumed progress that releases
    budget, or an exhausted ladder. Holds emit nothing, so the transcript stays quiet until
    something actually changes. Optional by construction: any failure yields None and the
    ratchet runs exactly as before.
    """

    def __init__(self, *, effort: str = "medium", concurrency: int = 4,
                 has_verifier: bool = True) -> None:
        self.controller = AdaptiveBudgetController(concurrency=concurrency)
        try:
            self.controller.allocate(_effort_to_probe(effort, has_verifier=has_verifier))
        except Exception:  # noqa: BLE001 — allocation must never break /ratchet
            pass

    def observe_round(self, passed: int, total: int) -> str | None:
        """Feed one round; return a note iff the allocation recommendation changed."""
        try:
            d = self.controller.observe(passed=int(passed), total=int(total))
        except Exception:  # noqa: BLE001 — advice must never break /ratchet
            return None
        return self.describe(d)

    @staticmethod
    def describe(d: BudgetDecision) -> str | None:
        if d.action == "escalate":
            env = d.envelope
            detail = _ADVICE.get(d.axis, d.reason)
            return (f"⚖ budget: plateau — {detail} "
                    f"[reasoning={env.reasoning.level}, context={env.context_modules}mod, "
                    f"agents={env.agents}, tools={env.tool_calls}]")
        if d.action == "deescalate":
            env = d.envelope
            return (f"⚖ budget: progress resumed — releasing raised budget "
                    f"[reasoning={env.reasoning.level}, agents={env.agents}]")
        if d.action == "terminate":
            return ("⚖ budget: escalation ladder exhausted — no resource left to raise; "
                    "the plateau is not a budget problem")
        return None

    def turn_config(self) -> dict:
        """The controller's current reasoning turn-config (§5) — what a ratchet worker turn
        should merge in to actuate ABC's reasoning level. Advisory callers can ignore it."""
        try:
            return self.controller.turn_config()
        except Exception:  # noqa: BLE001
            return {}

    def ledger(self) -> dict:
        return self.controller.ledger()

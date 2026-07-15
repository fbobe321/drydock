"""Explicit agent phases (PRD Epic B) — the task's lifecycle as a real state
machine the CONTROLLER owns, not ad-hoc strings the model sets.

The model may *recommend* a phase; the PhaseController decides whether that
transition is allowed. The one rule that matters most (PRD Test B1.4): the model
can never move itself to COMPLETE — completion requires verification evidence, so
a bare "I'm done" is routed to VERIFY instead.

AgentPhase is a str-Enum, so its members compare equal to the plain strings the
codebase already uses ("understand", "verify", …) and serialize unchanged — this
formalizes the phase set without breaking existing comparisons or saved state.
All logic original to Drydock.
"""
from __future__ import annotations

from enum import Enum


class AgentPhase(str, Enum):
    UNDERSTAND = "understand"
    DISCOVER = "discover"
    PLAN = "plan"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    REPAIR = "repair"
    COMPLETE = "complete"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def __str__(self) -> str:  # so f"{phase}" is "verify", not "AgentPhase.VERIFY"
        return self.value


TERMINAL = frozenset({AgentPhase.COMPLETE, AgentPhase.FAILED, AgentPhase.CANCELLED})

# Allowed forward transitions. Any phase may always be forced to a terminal
# BLOCKED/FAILED/CANCELLED (added below), so this graph only lists the productive
# moves. COMPLETE is intentionally reachable ONLY from VERIFY.
_ALLOWED: dict[AgentPhase, set[AgentPhase]] = {
    AgentPhase.UNDERSTAND: {AgentPhase.DISCOVER, AgentPhase.PLAN, AgentPhase.IMPLEMENT},
    AgentPhase.DISCOVER: {AgentPhase.PLAN, AgentPhase.IMPLEMENT, AgentPhase.UNDERSTAND},
    AgentPhase.PLAN: {AgentPhase.IMPLEMENT, AgentPhase.DISCOVER},
    AgentPhase.IMPLEMENT: {AgentPhase.VERIFY, AgentPhase.DISCOVER, AgentPhase.REPAIR},
    AgentPhase.VERIFY: {AgentPhase.COMPLETE, AgentPhase.REPAIR, AgentPhase.IMPLEMENT},
    AgentPhase.REPAIR: {AgentPhase.VERIFY, AgentPhase.IMPLEMENT, AgentPhase.DISCOVER},
    AgentPhase.COMPLETE: set(),
    AgentPhase.BLOCKED: {AgentPhase.UNDERSTAND, AgentPhase.IMPLEMENT},  # a user nudge can revive it
    AgentPhase.FAILED: set(),
    AgentPhase.CANCELLED: set(),
}
# Every non-terminal phase may be forced to a blocked/failed/cancelled stop.
for _p, _targets in _ALLOWED.items():
    if _p not in TERMINAL:
        _targets |= {AgentPhase.BLOCKED, AgentPhase.FAILED, AgentPhase.CANCELLED}


def as_phase(value) -> AgentPhase:
    """Coerce a str/AgentPhase to AgentPhase; unknown -> UNDERSTAND (never raises)."""
    if isinstance(value, AgentPhase):
        return value
    try:
        return AgentPhase(str(value))
    except ValueError:
        return AgentPhase.UNDERSTAND


class PhaseController:
    """Owns phase transitions (PRD Req B1). The model proposes; this approves.

    Stateless with respect to task data — it just enforces the transition graph
    and the completion-needs-evidence rule. Returns the phase the task should
    actually be in; an invalid proposal leaves the phase unchanged (advisory,
    never raises)."""

    def is_terminal(self, phase) -> bool:
        return as_phase(phase) in TERMINAL

    def advance(self, current, proposed, *, verified: bool = False) -> AgentPhase:
        """Return the approved next phase given `current` and the model's
        `proposed` target. COMPLETE is granted only when `verified` is True
        (evidence exists); otherwise it's redirected to VERIFY."""
        current = as_phase(current)
        proposed = as_phase(proposed)
        if proposed == current:
            return current
        if current in TERMINAL:
            return current  # no transitions out of a terminal phase
        # Governance rule B1.4: completion requires verification evidence.
        if proposed == AgentPhase.COMPLETE and not verified:
            return AgentPhase.VERIFY
        if proposed in _ALLOWED.get(current, set()):
            return proposed
        return current  # reject invalid transition — stay put

    def allowed_targets(self, current) -> set[AgentPhase]:
        return set(_ALLOWED.get(as_phase(current), set()))

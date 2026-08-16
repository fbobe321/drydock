"""Drydock Comms — typed events (the communication interface, separate from the
agent's raw output). See the Comms PRD §5/§24.

Design invariants (enforced in attention.py, asserted in tests):
  * Certain severities are NON-SUPPRESSIBLE — the attention policy can never
    downgrade them to LOG/IGNORE, and their summary is delivered faithfully
    (never paraphrased-away). Silence is never approval.
  * Everything else defaults to LOG/IGNORE unless it earns a louder channel.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    """Human-authority ladder (PRD §24). Order matters: >= APPROVAL_REQUIRED is
    non-suppressible."""
    INFORMATIONAL = 0
    ADVISORY = 1
    APPROVAL_REQUIRED = 2
    BLOCKING = 3
    CRITICAL = 4


# Severities the policy may NEVER suppress or downgrade below MESSAGE, and whose
# summary must be delivered verbatim (no lossy LLM paraphrase changing meaning).
NON_SUPPRESSIBLE = frozenset(
    {Severity.APPROVAL_REQUIRED, Severity.BLOCKING, Severity.CRITICAL}
)


class Decision(IntEnum):
    """Where an event ends up. IGNORE < LOG < DISPLAY < MESSAGE < SPEAK by
    'loudness'; a non-suppressible event is floored at MESSAGE."""
    IGNORE = 0
    LOG = 1
    DISPLAY = 2
    MESSAGE = 3
    SPEAK = 4


class Presence(IntEnum):
    UNKNOWN = 0
    AWAY = 1
    NEARBY = 2
    ACTIVE = 3          # at the TUI
    VOICE = 4           # in a live voice session
    DO_NOT_DISTURB = 5


# A small, stable event vocabulary. Not exhaustive — apps may emit others; the
# policy keys off severity/requires_response, not the exact string.
KNOWN_TYPES = frozenset({
    "task.started", "task.progress", "task.completed",
    "decision.required", "clarification.required", "permission.required",
    "test.failed", "test.passed",
    "error.recoverable", "error.blocking", "security.warning",
    "agent.stuck", "agent.recovered", "milestone.reached",
    "user.message",
})


@dataclass
class Event:
    """A structured piece of agent activity offered to the comms layer."""
    type: str
    severity: Severity = Severity.INFORMATIONAL
    summary: str = ""
    details: str = ""
    requires_response: bool = False
    interruptibility: str = "normal"      # "low" | "normal" | "high"
    source: str = "agent"
    task_id: str = ""
    confidence: float = 1.0
    ts: float = field(default_factory=time.time)

    def dedup_key(self) -> str:
        """Anti-annoyance key (PRD §25): same class + same task + same gist →
        aggregate rather than re-notify. Excludes CRITICAL/approval (those are
        never deduped away)."""
        return f"{self.type}|{self.task_id}|{self.summary[:80]}"

    def is_non_suppressible(self) -> bool:
        return self.severity in NON_SUPPRESSIBLE

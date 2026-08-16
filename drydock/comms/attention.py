"""Drydock Comms — the Attention Manager (PRD §6/§7/§25).

A DELIBERATELY DETERMINISTIC policy: event + context → Decision. No LLM in the
hot path (that would be latency, cost, and GPU contention with the coding model,
and it would make 'why did it stay silent?' untestable). An LLM is used only to
*word* the summary and to adjudicate the ambiguous mid-band — callers layer that
on top; this module decides the CHANNEL.

Guarantees (asserted in tests):
  * Non-suppressible severities never fall below MESSAGE, regardless of cooldown,
    presence (except DND still delivers), or dedup.
  * Routine, low-severity, non-response events default to LOG/IGNORE.
  * Repeated identical events are aggregated, not re-fired, until an escalation
    threshold — then a single 'still happening' notice, once per escalation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .events import Decision, Event, Presence, Severity


@dataclass
class AttentionConfig:
    # score→channel thresholds (PRD §29). Score is 0..5.
    speak_at: int = 3
    message_at: int = 4
    # anti-annoyance
    cooldown_secs: float = 300.0          # per dedup-key silence window
    escalate_after: int = 4               # Nth suppressed dup → one aggregated notice
    max_notifies_per_hour: int = 20       # global circuit breaker


@dataclass
class _KeyState:
    last_notified: float = 0.0
    suppressed: int = 0                   # dups seen inside the cooldown window


@dataclass
class AttentionPolicy:
    cfg: AttentionConfig = field(default_factory=AttentionConfig)
    _keys: dict = field(default_factory=dict)
    _notify_times: list = field(default_factory=list)   # for the hourly breaker

    # ── scoring (0..5) ──────────────────────────────────────────────────────
    def _score(self, e: Event) -> int:
        s = int(e.severity)              # 0..4 baseline from the authority ladder
        if e.requires_response:
            s = max(s, int(Severity.APPROVAL_REQUIRED))
        if e.type in ("agent.stuck", "error.blocking", "security.warning"):
            s = max(s, 3)
        if e.type in ("task.completed", "milestone.reached"):
            s = max(s, 2)
        if e.type in ("task.progress", "test.passed", "test.started"):
            s = min(s, 1)
        return max(0, min(5, s))

    def _channel_for_score(self, score: int, presence: Presence) -> Decision:
        if score >= self.cfg.message_at:
            # loud enough to reach a human: voice if they're listening, else message
            return Decision.SPEAK if presence == Presence.VOICE else Decision.MESSAGE
        if score >= self.cfg.speak_at:
            if presence == Presence.VOICE:
                return Decision.SPEAK
            if presence == Presence.ACTIVE:
                return Decision.DISPLAY
            return Decision.MESSAGE       # significant but user not at the box
        if score >= 2:
            return Decision.DISPLAY
        if score >= 1:
            return Decision.LOG
        return Decision.IGNORE

    def _hourly_ok(self, now: float) -> bool:
        self._notify_times = [t for t in self._notify_times if now - t < 3600]
        return len(self._notify_times) < self.cfg.max_notifies_per_hour

    def decide(self, e: Event, presence: Presence = Presence.UNKNOWN,
               now: float | None = None) -> Decision:
        now = time.time() if now is None else now

        # ── INVARIANT 1: non-suppressible severities always reach the human ──
        # No cooldown/dedup/breaker/presence (except an explicit DND still gets a
        # message — a blocking/approval/security event must not be lost).
        if e.is_non_suppressible():
            dec = Decision.SPEAK if presence == Presence.VOICE else Decision.MESSAGE
            self._note_notify(e, now)
            return dec

        score = self._score(e)

        # ── anti-annoyance: cooldown + dedup + aggregation ──────────────────
        key = e.dedup_key()
        st = self._keys.get(key)
        if st and (now - st.last_notified) < self.cfg.cooldown_secs:
            st.suppressed += 1
            # escalate ONCE when repetition crosses the threshold (PRD §25)
            if st.suppressed == self.cfg.escalate_after and score >= self.cfg.speak_at:
                st.last_notified = now
                return (Decision.MESSAGE if presence != Presence.ACTIVE
                        else Decision.DISPLAY)
            return Decision.LOG          # inside cooldown → quietly log the dup

        dec = self._channel_for_score(score, presence)

        # global circuit breaker for loud channels (never applies to invariant 1)
        if dec >= Decision.MESSAGE and not self._hourly_ok(now):
            dec = Decision.DISPLAY

        if dec >= Decision.DISPLAY:
            self._keys[key] = _KeyState(last_notified=now)
            if dec >= Decision.MESSAGE:
                self._note_notify(e, now)
        return dec

    def _note_notify(self, e: Event, now: float) -> None:
        self._notify_times.append(now)
        self._keys[e.dedup_key()] = _KeyState(last_notified=now)

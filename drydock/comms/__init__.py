"""Drydock Comms — the communication layer (PRD): decide what the human needs to
know, and how to tell them, separately from the agent's raw output.

Public API (PRD §28):
    from drydock import comms
    comms.emit_event("decision.required", severity=comms.Severity.APPROVAL_REQUIRED,
                     summary="Preserve the old API, or simplify and update callers?",
                     requires_response=True, task_id="passkeys")

emit_event runs the deterministic AttentionPolicy and routes to a channel; it
returns the Decision. Comms failures never raise into the caller.
"""
from __future__ import annotations

from .events import Decision, Event, Presence, Severity, NON_SUPPRESSIBLE
from .attention import AttentionPolicy, AttentionConfig
from .channels import Delivery, LogChannel, MessageProvider

_policy = AttentionPolicy()
_delivery = Delivery()
_presence = Presence.UNKNOWN


def configure(policy: AttentionPolicy | None = None,
              delivery: Delivery | None = None) -> None:
    global _policy, _delivery
    if policy is not None:
        _policy = policy
    if delivery is not None:
        _delivery = delivery


def set_presence(p: Presence) -> None:
    global _presence
    _presence = p


def emit_event(type: str, severity: Severity = Severity.INFORMATIONAL,
               summary: str = "", details: str = "", requires_response: bool = False,
               interruptibility: str = "normal", source: str = "agent",
               task_id: str = "") -> Decision:
    """Offer an event to the comms layer; returns where it went. Never raises."""
    try:
        e = Event(type=type, severity=severity, summary=summary, details=details,
                  requires_response=requires_response, interruptibility=interruptibility,
                  source=source, task_id=task_id)
        decision = _policy.decide(e, _presence)
        _delivery.deliver(e, decision)
        return decision
    except Exception:  # noqa: BLE001 — a broken comms layer must not break the task
        return Decision.LOG


__all__ = [
    "Decision", "Event", "Presence", "Severity", "NON_SUPPRESSIBLE",
    "AttentionPolicy", "AttentionConfig", "Delivery", "MessageProvider",
    "LogChannel", "emit_event", "configure", "set_presence",
]

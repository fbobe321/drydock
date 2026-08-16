"""Drydock Comms — channel delivery (PRD §15/§17). Routes a Decision to a place:
LOG→file, DISPLAY→stdout/TUI, MESSAGE→messaging provider, SPEAK→TTS (stub).

PROVENANCE: the published core ships NO outbound network sender — drydock's
identity is clean, no-phone-home code. Messaging is a PLUGGABLE ADAPTER: core
defines the `MessageProvider` interface only; the application (or the fleet)
injects a concrete sender via `comms.configure(...)`. A MESSAGE decision with no
injected provider falls back to display+log, so nothing is silently lost.
"""
from __future__ import annotations

import json

from .events import Decision, Event


class MessageProvider:
    """Interface for a messaging channel. Concrete network senders live OUTSIDE
    the published core (injected by the app) — the core stays phone-home-free."""
    def send(self, text: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class LogChannel:
    def __init__(self, path: str = "/tmp/drydock_comms.log"):
        self.path = path

    def write(self, event: Event, decision: Decision) -> None:
        try:
            with open(self.path, "a") as f:
                f.write(json.dumps({
                    "ts": event.ts, "decision": decision.name, "type": event.type,
                    "sev": int(event.severity), "task": event.task_id,
                    "summary": event.summary,
                }) + "\n")
        except Exception:  # noqa: BLE001
            pass


def format_for_human(event: Event) -> str:
    """The text a human sees/hears. For non-suppressible events we deliver the
    summary VERBATIM (no paraphrase that could change what's being authorized).
    Callers may substitute an LLM-worded summary for LOW-severity events only."""
    who = f"[{event.task_id}] " if event.task_id else ""
    if event.requires_response:
        return f"{who}{event.summary}".strip()
    return f"{who}{event.summary}".strip()


class Delivery:
    """Executes a Decision. Returns True if it reached the intended channel.

    message_provider is None by default (the clean core has no network sender);
    apps inject one via comms.configure(). With no provider, MESSAGE/SPEAK fall
    back to display+log so a non-suppressible event is never silently dropped."""
    def __init__(self, message_provider: MessageProvider | None = None,
                 log: LogChannel | None = None, display=print, speak=None):
        self.message_provider = message_provider
        self.log = log or LogChannel()
        self.display = display
        self.speak = speak

    def _send_message(self, text: str) -> bool:
        if self.message_provider is not None:
            return self.message_provider.send(text)
        self.display(f"drydock [no messaging provider]: {text}")   # never lose it
        return False

    def deliver(self, event: Event, decision: Decision) -> bool:
        self.log.write(event, decision)          # everything is always logged
        text = format_for_human(event)
        if decision == Decision.IGNORE or decision == Decision.LOG:
            return True
        if decision == Decision.DISPLAY:
            self.display(f"drydock: {text}")
            return True
        if decision == Decision.MESSAGE:
            return self._send_message(f"Drydock: {text}")
        if decision == Decision.SPEAK:
            if self.speak:
                self.speak(text)
                return True
            return self._send_message(f"Drydock: {text}")   # no TTS yet → message
        return False

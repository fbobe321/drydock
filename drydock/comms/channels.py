"""Drydock Comms — channel delivery (PRD §15/§17). Routes a Decision to a place:
LOG→file, DISPLAY→stdout/TUI, MESSAGE→messaging provider, SPEAK→TTS (stub).

Providers are pluggable (MessageProvider.send). The Telegram provider is
config-driven (token+chat via env or explicit args) so it works headless on the
fleet and can't accidentally hard-depend on a cloud service.
"""
from __future__ import annotations

import os
import json
import urllib.request
import urllib.parse

from .events import Decision, Event


class MessageProvider:
    def send(self, text: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class TelegramProvider(MessageProvider):
    """Minimal, dependency-free Telegram sender (stdlib urllib). Reads token/chat
    from args or env (DRYDOCK_TG_TOKEN / DRYDOCK_TG_CHAT)."""

    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or os.environ.get("DRYDOCK_TG_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("DRYDOCK_TG_CHAT", "")

    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.configured():
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text}).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=10) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001 — comms failure must never crash the task
            return False


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
    """Executes a Decision. Returns True if it reached the intended channel."""
    def __init__(self, message_provider: MessageProvider | None = None,
                 log: LogChannel | None = None, display=print, speak=None):
        self.message_provider = message_provider or TelegramProvider()
        self.log = log or LogChannel()
        self.display = display
        self.speak = speak

    def deliver(self, event: Event, decision: Decision) -> bool:
        self.log.write(event, decision)          # everything is always logged
        text = format_for_human(event)
        if decision == Decision.IGNORE or decision == Decision.LOG:
            return True
        if decision == Decision.DISPLAY:
            self.display(f"drydock: {text}")
            return True
        if decision == Decision.MESSAGE:
            return self.message_provider.send(f"Drydock: {text}")
        if decision == Decision.SPEAK:
            if self.speak:
                self.speak(text)
                return True
            # no TTS wired yet → fall back to message so it isn't lost
            return self.message_provider.send(f"Drydock: {text}")
        return False

"""Inject the Modular Context Runtime's working set into a real prompt.

This is the first time MCR *changes* what the model sees rather than merely recording
alongside it. Until now `build_view` was called only from tests.

PLACEMENT IS DICTATED BY MEASUREMENT, not taste. Live telemetry showed Drydock's loop is
a pure append at ~95% steady-state prefix reuse (cache-aware spec §0). MCR content
changes every round, so putting it in the system prompt — the prompt HEAD — would
diverge the prefix at message 0 on every turn and cost a full re-prefill (Appendix A.1:
a head mutation is 0.98x a cold prefill vs 0.24x at the tail). It is therefore appended
as the LAST message, keeping the entire stable prefix intact. Spec §16: the less settled
the content, the later it belongs.

OFF by default: enable with config `context_runtime` or `DRYDOCK_CONTEXT_RUNTIME=1`.
When no store is registered, or the view is empty, injection is a strict no-op — round
one of a ratchet is byte-identical to an uninstrumented run.
"""
from __future__ import annotations

import os
import threading

from drydock.context_runtime import (
    PINNED,
    SHARED,
    TOMBSTONED,
    ContextStore,
    build_view,
)

# cwd -> the store of the run currently executing there
_ACTIVE: "dict[str, ContextStore]" = {}
_LOCK = threading.Lock()

# What MCR is actually for: carrying forward what was already tried and ruled out, plus
# settled shared knowledge. The live conversation already holds the current work, so
# WORKING is deliberately excluded — re-sending it would duplicate the transcript.
PACKET_CLASSES = (PINNED, SHARED, TOMBSTONED)

DEFAULT_PACKET_BUDGET = 2000       # tokens; small on purpose — this rides in the tail


def register(cwd: str, store: ContextStore) -> None:
    with _LOCK:
        _ACTIVE[str(cwd)] = store


def unregister(cwd: str) -> None:
    with _LOCK:
        _ACTIVE.pop(str(cwd), None)


def active_store(cwd: str) -> "ContextStore | None":
    with _LOCK:
        return _ACTIVE.get(str(cwd))


def enabled(config: dict | None) -> bool:
    cfg = config or {}
    return bool(cfg.get("context_runtime") or os.environ.get("DRYDOCK_CONTEXT_RUNTIME"))


def build_packet(store: ContextStore, budget: int = DEFAULT_PACKET_BUDGET) -> str:
    """Render the carry-forward view, or "" when there is nothing worth saying."""
    view = build_view(store, budget=budget, order=PACKET_CLASSES)
    body = view.render().strip()
    if not body:
        return ""
    return ("## Carried-forward context (Drydock context runtime)\n"
            "Verified state and approaches already ruled out in this run. "
            "Do not re-attempt a ruled-out approach unless its revisit condition is met.\n\n"
            + body)


def inject(config: dict | None, oai_messages: list) -> list:
    """Append the MCR packet to the outgoing messages. Returns the list unchanged when
    disabled, when no store is registered, or when the packet is empty. Never raises."""
    if not enabled(config):
        return oai_messages
    try:
        store = active_store(str((config or {}).get("cwd") or "."))
        if store is None:
            return oai_messages
        budget = int((config or {}).get("context_packet_budget") or DEFAULT_PACKET_BUDGET)
        packet = build_packet(store, budget=budget)
        if not packet:
            return oai_messages
        return [*list(oai_messages or []), {"role": "system", "content": packet}]
    except Exception:  # noqa: BLE001 — injection must never break a run
        return oai_messages

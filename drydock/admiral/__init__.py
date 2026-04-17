"""Admiral — meta-controller that supervises drydock sessions.

Phase 1: heuristic loop and struggle detection, auto-applied interventions
via the existing `_inject_system_note` channel. All interventions are
logged to `~/.drydock/logs/admiral_history.log` for post-hoc review.

The detectors read the live message list of an AgentLoop; the worker
runs as a Textual background task so it lives in the same event loop
as the agent (no threading, no IPC). See Admiral.md for the PRD.
"""
from __future__ import annotations

from drydock.admiral.worker import AdmiralWorker, attach

__all__ = ["AdmiralWorker", "attach"]

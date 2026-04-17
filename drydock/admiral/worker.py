"""AdmiralWorker — periodic supervisor running inside the Textual app.

Phase 1 behaviour:
- Every ~5s, inspect the live AgentLoop message list.
- Run all detectors.
- For each finding NOT seen in the last `DEDUP_WINDOW_SEC` seconds,
  auto-apply the intervention.
- Log both findings (with auto-apply decision) and bootstrap/stop
  events to ~/.vibe/logs/admiral_history.log.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from drydock.admiral import detectors, history, interventions

if TYPE_CHECKING:
    from drydock.core.agent_loop import AgentLoop

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 5.0
DEDUP_WINDOW_SEC = 60.0


class AdmiralWorker:
    """Async task that supervises one AgentLoop.

    Not a Textual worker class — it's just an asyncio.Task you spawn
    from inside the TUI app's event loop. Lives alongside the agent,
    stops when the agent stops.
    """

    def __init__(self, agent_loop: AgentLoop) -> None:
        self.agent_loop = agent_loop
        self._task: asyncio.Task | None = None
        self._recent_findings: dict[str, float] = {}  # code -> timestamp

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="admiral-worker")
        history.append("bootstrap", "AdmiralWorker started")

    def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        self._task = None
        history.append("shutdown", "AdmiralWorker stopped")

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(POLL_INTERVAL_SEC)
                try:
                    self._tick()
                except Exception as e:
                    # Detector bugs must never crash the agent — log and continue.
                    logger.warning("Admiral tick error: %s", e)
                    history.append("error", f"tick failed: {e}")
        except asyncio.CancelledError:
            raise

    def _tick(self) -> None:
        now = time.monotonic()
        # Garbage-collect old dedup entries so long sessions don't leak.
        self._recent_findings = {
            code: ts for code, ts in self._recent_findings.items()
            if now - ts < DEDUP_WINDOW_SEC
        }
        findings = detectors.run_all(list(self.agent_loop.messages))
        for f in findings:
            last = self._recent_findings.get(f.code)
            if last is not None and (now - last) < DEDUP_WINDOW_SEC:
                continue
            self._recent_findings[f.code] = now
            interventions.apply(self.agent_loop, f)


def attach(agent_loop: AgentLoop) -> AdmiralWorker:
    """Start an AdmiralWorker supervising the given agent loop.

    Returns the worker handle so the caller can stop it on shutdown.
    Safe to call multiple times — a fresh worker replaces any prior
    one, but that's the caller's responsibility to track.
    """
    worker = AdmiralWorker(agent_loop)
    worker.start()
    return worker

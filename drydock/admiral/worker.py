"""AdmiralWorker — periodic supervisor running inside the Textual app.

Behaviour:
- Every ~5s, inspect the live AgentLoop message list.
- Run all detectors.
- For each finding NOT seen in the last `DEDUP_WINDOW_SEC` seconds,
  choose an intervention directive via this escalation ladder:
    1. Ask the local LLM for a diagnosis (llm_analyzer.analyze).
    2. If local is stumped, escalate to Claude Code Opus
       (opus_escalator.escalate) — capped at MAX_OPUS_PER_SESSION.
    3. Fall back to the finding's canned directive.
- Apply the chosen directive via _inject_system_note and log it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from drydock.admiral import detectors, history, interventions, llm_analyzer, opus_escalator

if TYPE_CHECKING:
    from drydock.admiral.detectors import Finding
    from drydock.core.agent_loop import AgentLoop

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 5.0
DEDUP_WINDOW_SEC = 60.0
MAX_OPUS_PER_SESSION = opus_escalator.MAX_ESCALATIONS_PER_SESSION


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
        self._opus_calls_used: int = 0
        self._in_flight: set[str] = set()  # codes currently being escalated

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
            if f.code in self._in_flight:
                continue
            self._recent_findings[f.code] = now
            self._in_flight.add(f.code)
            asyncio.create_task(
                self._handle_finding(f), name=f"admiral-handle:{f.code[:32]}"
            )

    async def _handle_finding(self, finding: Finding) -> None:
        """Escalation ladder: local LLM → Opus → canned directive."""
        try:
            directive, source = await self._resolve_directive(finding)
            history.append(
                "directive-source",
                f"{finding.code} :: source={source}",
            )
            finding_with_text = type(finding)(code=finding.code, directive=directive)
            interventions.apply(self.agent_loop, finding_with_text)
        except Exception as e:
            logger.warning("Admiral handle_finding failed: %s", e)
            history.append("error", f"handle_finding failed: {finding.code} :: {e}")
        finally:
            self._in_flight.discard(finding.code)

    async def _resolve_directive(self, finding: Finding) -> tuple[str, str]:
        """Return (directive_text, source) for the finding.

        Sources: "local-llm" > "opus" > "canned" (fallback).
        """
        # 1. Ask the local LLM first.
        try:
            proposal = await llm_analyzer.analyze(self.agent_loop, finding)
        except Exception as e:
            logger.warning("Admiral llm_analyzer crashed: %s", e)
            proposal = None
        if proposal:
            return proposal, "local-llm"

        # 2. Escalate to Opus if we have budget.
        if self._opus_calls_used < MAX_OPUS_PER_SESSION:
            self._opus_calls_used += 1
            try:
                opus_proposal = await opus_escalator.escalate(
                    finding, list(self.agent_loop.messages)
                )
            except Exception as e:
                logger.warning("Admiral opus escalation crashed: %s", e)
                opus_proposal = None
            if opus_proposal:
                return opus_proposal, "opus"

        # 3. Fall back to the detector's canned directive.
        return finding.directive, "canned"


def attach(agent_loop: AgentLoop) -> AdmiralWorker:
    """Start an AdmiralWorker supervising the given agent loop.

    Returns the worker handle so the caller can stop it on shutdown.
    Safe to call multiple times — a fresh worker replaces any prior
    one, but that's the caller's responsibility to track.
    """
    worker = AdmiralWorker(agent_loop)
    worker.start()
    return worker

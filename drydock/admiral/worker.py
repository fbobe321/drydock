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

from drydock.admiral import detectors, history, interventions, llm_analyzer, opus_escalator, persistence

if TYPE_CHECKING:
    from drydock.admiral.detectors import Finding
    from drydock.core.agent_loop import AgentLoop

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 5.0
DEDUP_WINDOW_SEC = 60.0
MAX_OPUS_PER_SESSION = opus_escalator.MAX_ESCALATIONS_PER_SESSION

# Intervention-outcome window: per persistence.py docstring, "same code
# re-firing within 10 turns after an intervention = failed." A turn is
# one assistant message; we count messages (any role) as a proxy because
# a re-fire necessarily comes after at least 1 assistant turn.
INTERVENTION_FAIL_WINDOW_TURNS = 10


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
        # Pending interventions awaiting outcome classification.
        # code -> message-count at apply time. On re-fire within
        # INTERVENTION_FAIL_WINDOW_TURNS messages → failed; otherwise → unstuck.
        self._pending_interventions: dict[str, int] = {}

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

        # Resolve any pending interventions whose fail-window has elapsed
        # without a re-fire — those count as `unstuck=True`.
        msg_count_now = len(self.agent_loop.messages)
        self._resolve_elapsed_interventions(msg_count_now)

        findings = detectors.run_all(list(self.agent_loop.messages))
        for f in findings:
            # Re-fire while a prior intervention is still inside its
            # fail window → that intervention failed.
            applied_at = self._pending_interventions.get(f.code)
            if applied_at is not None and (msg_count_now - applied_at) <= INTERVENTION_FAIL_WINDOW_TURNS:
                self._record_outcome(f.code, unstuck=False)
                self._pending_interventions.pop(f.code, None)

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

    def _resolve_elapsed_interventions(self, msg_count_now: int) -> None:
        """Mark interventions as `unstuck=True` if their fail window
        elapsed without the same code re-firing."""
        elapsed: list[str] = []
        for code, applied_at in self._pending_interventions.items():
            if (msg_count_now - applied_at) > INTERVENTION_FAIL_WINDOW_TURNS:
                elapsed.append(code)
        for code in elapsed:
            self._record_outcome(code, unstuck=True)
            self._pending_interventions.pop(code, None)

    def _record_outcome(self, code: str, *, unstuck: bool) -> None:
        """Persist the intervention outcome — never raise (Admiral must
        not crash drydock). Logs both outcomes to history for visibility."""
        try:
            persistence.record_intervention_outcome(code, unstuck=unstuck)
            history.append(
                "intervention-outcome",
                f"{code} :: {'unstuck' if unstuck else 'failed'}",
            )
        except Exception as e:
            logger.debug("record_intervention_outcome failed: %s", e)

    async def _handle_finding(self, finding: Finding) -> None:
        """Escalation ladder: local LLM → Opus → canned directive.

        Also records the finding to the cross-session state so the
        Phase 3b proposer can check promotion criteria, and — if the
        proposer is enabled AND criteria are met — drafts a code
        proposal in the background.
        """
        try:
            # Record for Phase 3b promotion criteria. Use the live
            # `session_id` (matches the on-disk session log dir) so
            # M5/Deep Noir can extract contrastive pairs from the dir.
            # Pre-fix this read `_admiral_session_id`, a phantom uuid
            # that never resolved to anything on disk.
            session_id = getattr(self.agent_loop, "session_id", "")
            persistence.record_finding(finding.code, session_id)

            directive, source = await self._resolve_directive(finding)
            history.append(
                "directive-source",
                f"{finding.code} :: source={source}",
            )
            finding_with_text = type(finding)(code=finding.code, directive=directive)
            interventions.apply(self.agent_loop, finding_with_text)

            # Watch for re-fires within the fail window to classify this
            # intervention's outcome. Without this, prompt_failed stays at
            # 0 forever and Phase 3b promotion criteria never trigger.
            self._pending_interventions[finding.code] = len(self.agent_loop.messages)

            # Phase 3b: if same finding qualifies and proposer is enabled,
            # kick off a code-proposal draft in the background.
            if persistence.finding_qualifies_for_code_change(finding.code):
                asyncio.create_task(
                    self._maybe_propose_code(finding_with_text),
                    name=f"admiral-propose:{finding.code[:32]}",
                )
        except Exception as e:
            logger.warning("Admiral handle_finding failed: %s", e)
            history.append("error", f"handle_finding failed: {finding.code} :: {e}")
        finally:
            self._in_flight.discard(finding.code)

    async def _maybe_propose_code(self, finding: Finding) -> None:
        """Phase 3b: draft → validate → stage. Every step logged."""
        try:
            from drydock.admiral import proposer, stager, validator
        except Exception as e:
            logger.debug("Admiral phase3b imports failed: %s", e)
            return
        try:
            proposal = await proposer.propose(self.agent_loop, finding)
            if not proposal:
                return
            history.append(
                "proposal-draft",
                f"{proposal.code} :: source={proposal.source} :: fp={proposal.fingerprint}",
            )
            # Validation runs in a thread — it's subprocess-heavy.
            import asyncio as _asyncio
            result = await _asyncio.to_thread(validator.validate, proposal.diff)
            if not result.ok:
                history.append(
                    "proposal-rejected",
                    f"{proposal.code} :: validation failed: {result.stderr[:200]}",
                )
                return
            staged = await _asyncio.to_thread(stager.stage, proposal, result)
            if staged:
                history.append(
                    "proposal-ready",
                    f"branch={staged.branch} :: {staged.proposal_path}",
                )
        except Exception as e:
            logger.warning("Admiral phase3b pipeline error: %s", e)
            history.append("error", f"phase3b: {e}")

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

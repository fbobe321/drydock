"""Phase 3b: draft a unified-diff proposal for a recurring finding.

Only called for findings that survived prompt-only interventions
(see `persistence.finding_qualifies_for_code_change`). Uses the
same escalation ladder as Phase 2 (local LLM → Opus).

Returns a `Proposal` or `None`. Caller (worker) passes the proposal
to validator.validate(), and if that returns green, to stager.stage().

Default DISABLED — reads `DRYDOCK_ADMIRAL_PROPOSER=1` env var.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from drydock.admiral import persistence
from drydock.admiral.llm_analyzer import _DIRECTIVES_RUBRIC, _render_turns
from drydock.admiral.opus_escalator import _try_anthropic_sdk, _try_claude_cli
from drydock.core.types import LLMMessage, Role

if TYPE_CHECKING:
    from drydock.admiral.detectors import Finding
    from drydock.core.agent_loop import AgentLoop

logger = logging.getLogger(__name__)

_PROPOSER_PROMPT = """You are Admiral's code proposer. A recurring finding has \
survived prompt-only interventions and should be fixed at the source. Draft a \
MINIMAL unified diff that addresses the root cause.

{rubric}

Recurring finding: {code}
Total fires across sessions: {n_fires}
Sessions involved: {n_sessions}
Failed prompt-only interventions: {n_failed}

Repro context (last turns):
<conversation>
{context}
</conversation>

Output in EXACTLY this format (no extra text):

DIRECTIVES VIOLATED: [code1,code2,...]
RATIONALE: <one paragraph grounded in the cited directives>
DIFF:
```diff
<unified diff — narrow scope, touches only the files needed>
```
"""


@dataclass
class Proposal:
    code: str
    directives_violated: list[str]
    rationale: str
    diff: str
    source: str          # "local-llm" | "opus"
    fingerprint: str     # sha256 of the diff — used for reject tracking


_DIFF_RE = re.compile(r"```(?:diff)?\n([\s\S]*?)```", re.MULTILINE)
_DIR_RE = re.compile(r"DIRECTIVES VIOLATED:\s*\[([^\]]*)\]")
_RAT_RE = re.compile(r"RATIONALE:\s*(.+?)(?:\nDIFF:|\Z)", re.DOTALL)


def _parse(text: str) -> tuple[list[str], str, str] | None:
    m_diff = _DIFF_RE.search(text)
    m_rat = _RAT_RE.search(text)
    m_dir = _DIR_RE.search(text)
    if not m_diff:
        return None
    diff = m_diff.group(1).strip()
    if not diff:
        return None
    rationale = (m_rat.group(1).strip() if m_rat else "")[:2000]
    dirs = []
    if m_dir:
        dirs = [s.strip() for s in m_dir.group(1).split(",") if s.strip()]
    return dirs, rationale, diff


def _fingerprint(diff: str) -> str:
    return hashlib.sha256(diff.strip().encode()).hexdigest()[:16]


async def _ask_local(agent_loop: AgentLoop, prompt: str) -> str | None:
    try:
        chunk = await agent_loop.backend.complete(
            model=agent_loop.config.get_active_model(),
            messages=[LLMMessage(role=Role.user, content=prompt)],
            temperature=0.1,
            max_tokens=2000,
        )
        return (chunk.message.content or "").strip() or None
    except Exception as e:
        logger.warning("Admiral proposer local call failed: %s", e)
        return None


async def _ask_opus(prompt: str) -> str | None:
    out = await _try_anthropic_sdk(prompt)
    if out:
        return out
    return await _try_claude_cli(prompt)


async def propose(agent_loop: AgentLoop, finding: Finding) -> Proposal | None:
    if os.getenv("DRYDOCK_ADMIRAL_PROPOSER", "0") != "1":
        return None

    state = persistence.load_state()
    entry = state.get("findings", {}).get(finding.code) or {}
    n_fires = int(entry.get("total_fires", 0))
    n_sessions = len(entry.get("sessions", []))
    n_failed = int(entry.get("prompt_failed", 0))

    prompt = _PROPOSER_PROMPT.format(
        rubric=_DIRECTIVES_RUBRIC,
        code=finding.code,
        n_fires=n_fires,
        n_sessions=n_sessions,
        n_failed=n_failed,
        context=_render_turns(list(agent_loop.messages), n_turns=12),
    )

    # 1) local LLM
    text = await _ask_local(agent_loop, prompt)
    source = "local-llm"
    parsed = _parse(text or "")
    # 2) Opus fallback
    if not parsed:
        text = await _ask_opus(prompt)
        source = "opus"
        parsed = _parse(text or "")
    if not parsed:
        return None
    dirs, rationale, diff = parsed
    fp = _fingerprint(diff)
    if persistence.is_fingerprint_rejected(finding.code, fp):
        logger.info("Admiral proposer: skipping previously-rejected patch %s", fp)
        return None
    return Proposal(
        code=finding.code,
        directives_violated=dirs,
        rationale=rationale,
        diff=diff,
        source=source,
        fingerprint=fp,
    )

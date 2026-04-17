"""Phase 2 escalation: ask Claude Code (Opus) when the local LLM is stumped.

Two transport options, tried in order:
1. Anthropic SDK via ANTHROPIC_API_KEY (fastest, no subprocess).
2. `claude -p "<prompt>"` CLI fallback (reuses the user's existing
   Claude Code auth, no second API key needed).

Rate-limited: no more than `MAX_ESCALATIONS_PER_SESSION` Opus calls
per AdmiralWorker instance.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Sequence
from typing import TYPE_CHECKING

from drydock.admiral.llm_analyzer import _DIRECTIVES_RUBRIC, _render_turns

if TYPE_CHECKING:
    from drydock.admiral.detectors import Finding
    from drydock.core.types import LLMMessage

logger = logging.getLogger(__name__)

MAX_ESCALATIONS_PER_SESSION = 3
OPUS_TIMEOUT_SEC = 90
OPUS_MODEL = "claude-opus-4-7"

_OPUS_PROMPT = """You are a senior engineer being paged by Admiral, a supervisor \
for a local coding agent (Gemma 4). The local model tried to diagnose a stuck \
session and couldn't figure it out. Give one short actionable directive the \
agent can follow to unstick itself — or, if the agent should stop and ask \
the user, say so explicitly.

{rubric}

Detector: {code}
Admiral's canned fallback: "{fallback}"

Recent conversation:
<conversation>
{context}
</conversation>

Reply with ONE directive on the FIRST line prefixed by the directive codes \
from the rubric that the agent is violating, e.g. "[B5,B8] Write the \
interpreter.py file now and stop re-reading the PRD." Imperative voice, \
1-3 sentences. No preamble, no markdown, no quotes — just the bracketed \
codes and the directive text.
"""


async def _try_anthropic_sdk(prompt: str) -> str | None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        r = await asyncio.wait_for(
            client.messages.create(
                model=OPUS_MODEL,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=OPUS_TIMEOUT_SEC,
        )
        if r.content and hasattr(r.content[0], "text"):
            return r.content[0].text.strip() or None
    except Exception as e:
        logger.warning("Admiral Opus SDK call failed: %s", e)
    return None


async def _try_claude_cli(prompt: str) -> str | None:
    """Invoke `claude -p "<prompt>"` as a subprocess if available."""
    if shutil.which("claude") is None:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=OPUS_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return None
        if proc.returncode != 0:
            return None
        out = stdout.decode("utf-8", errors="replace").strip()
        return out or None
    except Exception as e:
        logger.warning("Admiral claude-cli escalation failed: %s", e)
    return None


async def escalate(
    finding: Finding,
    messages: Sequence[LLMMessage],
) -> str | None:
    """Ask Opus for a directive. Returns directive text or None."""
    prompt = _OPUS_PROMPT.format(
        rubric=_DIRECTIVES_RUBRIC,
        code=finding.code,
        fallback=finding.directive[:200],
        context=_render_turns(messages, n_turns=12),
    )
    out = await _try_anthropic_sdk(prompt)
    if out:
        return out
    return await _try_claude_cli(prompt)

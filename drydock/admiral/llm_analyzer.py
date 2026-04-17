"""Phase 2: ask the local LLM to propose a directive for a finding.

Before Admiral auto-applies a canned directive, it hands the recent
conversation context and the detector code to the local model and
asks for a diagnosis. If the local model is also stumped (returns
the sentinel STUMPED line), the caller escalates to opus_escalator.

Reuses AgentLoop.backend so we inherit the model config and endpoint
the user already set up — no second key, no second server.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from drydock.core.types import LLMMessage, Role

if TYPE_CHECKING:
    from drydock.admiral.detectors import Finding
    from drydock.core.agent_loop import AgentLoop

logger = logging.getLogger(__name__)

_ANALYZER_PROMPT = """You are Admiral, a meta-controller that supervises a coding agent. \
A heuristic just flagged the agent as stuck. Your job: diagnose what is \
really going wrong and propose ONE short directive to unstick the agent.

Detector that fired: {code}
Initial directive Admiral was about to send: "{fallback}"

Recent conversation (last {n_turns} turns):
<conversation>
{context}
</conversation>

Respond in EXACTLY one of these two formats (no extra text):

DIRECTIVE: <your proposed directive — imperative voice, 1-3 sentences, \
concrete enough that the agent knows exactly what to do next>

STUMPED: <one sentence on why you can't diagnose this from the context>
"""


def _render_turns(messages: Sequence[LLMMessage], n_turns: int = 12) -> str:
    tail = list(messages)[-n_turns:]
    lines: list[str] = []
    for m in tail:
        role = m.role.value if hasattr(m.role, "value") else str(m.role)
        if m.tool_calls:
            for tc in m.tool_calls:
                name = tc.function.name or "?"
                args = (tc.function.arguments or "")[:200]
                lines.append(f"[{role}] TOOL {name}({args})")
        else:
            content = (m.content or "")[:300]
            lines.append(f"[{role}] {content}")
    return "\n".join(lines) or "<no history>"


async def analyze(agent_loop: AgentLoop, finding: Finding) -> str | None:
    """Ask the local LLM to propose a directive for the finding.

    Returns the LLM-proposed directive string, or `None` if the model
    returned STUMPED (caller should escalate) or if the call failed
    for any reason (caller should fall back to the finding's canned
    directive).
    """
    try:
        context = _render_turns(agent_loop.messages, n_turns=12)
        prompt = _ANALYZER_PROMPT.format(
            code=finding.code,
            fallback=finding.directive[:200],
            n_turns=12,
            context=context,
        )
        chunk = await agent_loop.backend.complete(
            model=agent_loop.config.get_active_model(),
            messages=[LLMMessage(role=Role.user, content=prompt)],
            temperature=0.1,
            max_tokens=400,
        )
    except Exception as e:
        logger.warning("Admiral LLM analysis failed: %s", e)
        return None

    text = (chunk.message.content or "").strip()
    if not text:
        return None
    # Strip reasoning tokens that some models leak.
    if text.startswith("STUMPED"):
        return None
    if "DIRECTIVE:" in text:
        proposal = text.split("DIRECTIVE:", 1)[1].strip()
        # Take only the first line/paragraph — discard chain-of-thought.
        proposal = proposal.split("\n\n")[0].strip()
        return proposal or None
    return None

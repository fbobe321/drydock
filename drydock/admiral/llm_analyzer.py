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

_DIRECTIVES_RUBRIC = """Admiral holds drydock to these numbered directives:

A. CORRECTNESS
 A1. Verify, don't assume — `--help` is not a test.
 A2. No fabrication — invented tools/files/APIs are failures.
 A3. Test-gated progress — writing a file is not a finished feature.
 A4. Never silently swallow errors.

B. REAL PROGRESS
 B5. Every tool call must reduce uncertainty or change state.
 B6. No loops — identical call x3 means pivot, not retry.
 B7. Respect user scope — one ask, one answer.
 B8. Commit to a plan — after ~20 reads without a write, either \
write code or report what's blocking.

C. SAFETY
 C9. Destructive ops need human approval.
 C10. Never bypass verification (no --no-verify, no hook skip).
 C11. Secrets stay out of logs, commits, messages.

D. TRANSPARENCY
 D12. Every action auditable.
 D13. Output readable (paragraph breaks, lists).
 D14. Honest status — "Task Completed" means actually completed.

E. RIGOR
 E15. Small composable edits.
 E16. Reuse existing patterns.
 E17. Minimal targeted fixes (no drive-by refactors).

F. CONTEXT HYGIENE
 F18. Truncate noise, keep load-bearing context.
 F19. Don't re-read unchanged files."""


_ANALYZER_PROMPT = """You are Admiral, a meta-controller that supervises a coding agent. \
A heuristic just flagged the agent as stuck. Your job: diagnose what is \
really going wrong and propose ONE short directive to unstick the agent.

{rubric}

Detector that fired: {code}
Initial directive Admiral was about to send: "{fallback}"

Recent conversation (last {n_turns} turns):
<conversation>
{context}
</conversation>

Respond in EXACTLY one of these two formats (no extra text):

DIRECTIVE [Bx,Cy]: <your proposed directive — imperative voice, 1-3 \
sentences, concrete enough that the agent knows exactly what to do \
next. The bracket lists which directive codes from the rubric above \
are being violated (e.g. [B5,B8] for exploring-without-writing).>

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
            rubric=_DIRECTIVES_RUBRIC,
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

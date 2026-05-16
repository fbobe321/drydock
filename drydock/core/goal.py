"""Goal pursuit — autonomous turn-after-turn work toward a condition.

Mirrors the Claude Code `/goal` feature. The user sets a completion
condition; after every turn, a small evaluator model decides whether
the condition holds. If not, drydock injects a continuation prompt
and runs another turn. The goal clears automatically once met.

Design notes:
  - Iteration cap (default 20) prevents infinite loops on unverifiable
    or impossible goals.
  - Evaluator uses the same provider/model as the agent loop. A small
    dedicated model would be cleaner but complicates local deployment.
    The evaluator prompt is tight: it returns a single YES/NO token.
  - The continuation prompt is synthesised — same shape as the existing
    DRYDOCK_AUTO_CONTINUE mechanism — but tagged with the goal so the
    main agent loop knows we're on a goal turn, not a user turn.
  - Status (`/goal` with no args), set (`/goal <condition>`), and
    clear (`/goal clear`) live in commands.py; this module owns the
    state, evaluation, and continuation-prompt synthesis.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("drydock.goal")


@dataclass
class GoalState:
    """Per-session goal state. Owned by the TUI / AgentLoop wrapper."""

    condition: str
    iterations: int = 0
    # Conservative cap — covers most realistic workflows (per-turn ~30-80s
    # on Gemma 4, so 20 turns ~10-25 min) without letting a runaway goal
    # burn the whole afternoon. Operator can override.
    max_iterations: int = 20
    last_verdict: str = ""        # "YES" | "NO" | "ERROR" | "" before first eval
    last_evaluator_reasoning: str = ""

    @property
    def active(self) -> bool:
        return bool(self.condition.strip())

    @property
    def remaining(self) -> int:
        return max(0, self.max_iterations - self.iterations)


# ── Evaluation prompt ───────────────────────────────────────────────────

EVALUATOR_SYSTEM_PROMPT = (
    "You are a goal-completion evaluator. You read a goal condition "
    "plus the recent conversation between a user and a coding agent, "
    "and you decide whether the goal has been met.\n\n"
    "Output rules (STRICT):\n"
    "1. Your FIRST line must be exactly `VERDICT: YES` or `VERDICT: NO`.\n"
    "2. Your SECOND line should be a one-sentence reason (≤120 chars).\n"
    "3. Output NOTHING else. No preamble, no markdown, no quotes.\n\n"
    "Be conservative — only say YES if the conversation contains "
    "concrete evidence the goal is satisfied. Tool output, test "
    "results, file contents, or explicit confirmation from the agent. "
    "Plans, intentions, and 'I will' statements are NOT evidence."
)


def build_evaluator_prompt(goal: GoalState, recent_messages: list[str]) -> str:
    """Render the evaluator user prompt from the goal + last N msgs."""
    convo = "\n\n---\n\n".join(recent_messages[-12:])
    if len(convo) > 8000:
        convo = convo[:8000] + "\n[... earlier turns truncated]"
    return (
        f"GOAL CONDITION:\n{goal.condition}\n\n"
        f"RECENT CONVERSATION:\n{convo}\n\n"
        f"Has the goal been met? Respond with VERDICT: YES or VERDICT: NO."
    )


def parse_verdict(raw: str) -> tuple[str, str]:
    """Parse an evaluator response into (verdict, reasoning).

    verdict ∈ {"YES", "NO", "ERROR"}.
    """
    if not raw:
        return ("ERROR", "evaluator returned empty response")
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if not lines:
        return ("ERROR", "evaluator returned only whitespace")
    first = lines[0].upper()
    reasoning = lines[1] if len(lines) > 1 else ""
    # Accept both strict ("VERDICT: YES") and lenient ("YES.") forms
    if first.startswith("VERDICT:"):
        verdict_token = first.replace("VERDICT:", "").strip().rstrip(".").strip()
    else:
        verdict_token = first.rstrip(".").strip()
    if verdict_token.startswith("YES"):
        return ("YES", reasoning)
    if verdict_token.startswith("NO"):
        return ("NO", reasoning)
    return ("ERROR", f"unrecognised verdict token: {first[:60]!r}")


def make_continuation_prompt(goal: GoalState) -> str:
    """The synthesised user message that keeps the agent working.

    Kept short so it doesn't dominate the next turn's context. The
    main agent loop's system prompt already explains the agent's
    role; the continuation just refocuses on the goal.
    """
    return (
        f"Continue working toward the goal: {goal.condition}\n"
        f"(Iteration {goal.iterations}/{goal.max_iterations}. "
        f"Goal will auto-clear when met.)"
    )


# ── Snippet extraction for the evaluator ────────────────────────────────

def collect_recent_message_snippets(messages, n: int = 8) -> list[str]:
    """Render the last n messages as short strings the evaluator can
    read. Truncates each to ≤600 chars so the evaluator prompt stays
    bounded.

    `messages` is the AgentLoop's `self.messages` — a sequence of
    LLMMessage objects with .role and .content.
    """
    out: list[str] = []
    # Skip the system message (index 0); take the tail.
    tail = list(messages)[-n:] if len(messages) > n else list(messages)
    for m in tail:
        role = getattr(m, "role", "?")
        role_str = getattr(role, "value", None) or str(role)
        content = getattr(m, "content", "") or ""
        # Include tool-call summaries since they're often the evidence
        # the evaluator needs (test pass, file written, etc.).
        tcs = getattr(m, "tool_calls", None) or []
        if tcs and not content:
            tc_summary = ", ".join(
                getattr(getattr(tc, "function", None), "name", "?")
                for tc in tcs
            )
            content = f"[tool calls: {tc_summary}]"
        if len(content) > 600:
            content = content[:600] + "...[truncated]"
        out.append(f"[{role_str}] {content}")
    return out

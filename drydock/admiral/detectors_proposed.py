"""Proposed Admiral detectors — NOT yet wired into run_all().

These are data-backed additions drafted from a 400-session mining run on
2026-04-23 (see /data3/Deep_Noir_1/drydock_probes/results/session_behavior_stats.json).

To activate: in `detectors.py::run_all`, add the chosen function(s) to the
tuple in the `for fn in (...)` line. Keep the existing `Finding(code, directive)`
advisory contract — no hard-blocks — consistent with PRD §4.1.E and
CLAUDE.md learning #19.

Observed rates across 400 real drydock sessions with Gemma 4:

  IDENTICAL_TOOL_REPEAT       481 total, 1.20/session  (existing loop detector
                                                       catches this but the
                                                       model ignores the nudge)
  EMPTY_ASSISTANT_AFTER_TOOL  254 total, 0.64/session  (new: model produces
                                                       no content+no tools;
                                                       drydock papers over
                                                       with a filler message)
  TOOL_ARGS_IGNORE_RESULT      45 total, 0.11/session  (new: model retries
                                                       the identical tool call
                                                       that just errored)

Legitimate-completion baseline (for precision control): 52 sessions end with
a clean "I have ..." / "The ... has been ..." summary.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Optional

from drydock.core.types import LLMMessage, Role
from drydock.admiral.detectors import Finding, _tool_sig


_TOOL_ERROR_MARKERS: tuple[str, ...] = (
    "error", "traceback", "exception", "failed",
    "cannot", "no such", "not found", "syntax",
)


def _tool_result_errored(m: LLMMessage) -> bool:
    """Heuristic: does this tool-role message look like a failure?

    Conservative — we only want high-precision firing on actual errors.
    Scanned the first 800 chars of the result content.
    """
    if m.role is not Role.tool:
        return False
    content = str(getattr(m, "content", "") or "")[:800].lower()
    return any(marker in content for marker in _TOOL_ERROR_MARKERS)


def detect_empty_after_tool(
    messages: Sequence[LLMMessage],
) -> Optional[Finding]:
    """Fires when the last assistant turn had no content AND no tool_calls,
    immediately following a tool result.

    In 296 sessions this fired 254 times (0.64/session). When it fires, the
    drydock filler `_ensure_assistant_after_tools` (agent_loop.py:3006) inserts
    "Previous turn ended; awaiting your next instruction." — a user-facing
    sign that the model's thinking channel produced nothing usable.

    Admiral intervention should:
      (a) inject a directive asking the model to summarize the tool output
          and commit to a next action, and
      (b) arguably prune the empty assistant + filler pair on the next rerun
          (interventions.py responsibility, not this detector's).
    """
    # Walk backwards to find the last assistant message; look at the one
    # before it.
    last_assistant_idx: Optional[int] = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role is Role.assistant:
            last_assistant_idx = i
            break
    if last_assistant_idx is None or last_assistant_idx == 0:
        return None
    last = messages[last_assistant_idx]
    content_str = str(getattr(last, "content", "") or "").strip()
    tool_calls = getattr(last, "tool_calls", None) or []
    prev = messages[last_assistant_idx - 1]
    if prev.role is not Role.tool:
        return None

    # Two ways this fires:
    # (1) Live: the assistant returned neither content nor tool_calls. Admiral
    #     sees this BEFORE drydock's _ensure_assistant_after_tools appends
    #     its filler.
    # (2) Retrospective: drydock has already filled with the canonical
    #     "Previous turn ended..." marker. The model's original turn was
    #     empty; the filler is now standing in its place.
    DRYDOCK_FILLER_PREFIX = "Previous turn ended; awaiting"
    is_empty = (not content_str) and (not tool_calls)
    is_filler = content_str.startswith(DRYDOCK_FILLER_PREFIX) and (not tool_calls)
    if not (is_empty or is_filler):
        return None

    tool_name = str(getattr(prev, "name", "") or "?")
    return Finding(
        code=f"empty_after_tool:{tool_name}",
        directive=(
            "Admiral: your last turn produced no content and no tool call "
            f"after the `{tool_name}` result. Your next turn MUST either "
            "(a) call another tool to make concrete progress, or "
            "(b) emit a short text summary of what the tool returned plus "
            "what you will do next. Do not hand back control silently."
        ),
    )


def detect_retry_after_error(
    messages: Sequence[LLMMessage],
) -> Optional[Finding]:
    """Fires when an identical tool call follows an errored tool result.

    In 400 sessions this fired 45 times (0.11/session). High-signal because
    repeating the exact same call after an error is almost never the right
    response. The existing detect_tool_call_loop only fires at window=3;
    this catches the 2-call version where the error context is explicit.

    Admiral intervention: inject the specific error snippet and tell the
    model to change approach before the next call.
    """
    # Walk backwards looking for: last_assistant_with_tool_calls,
    # immediately-preceding tool-role result, and a prior-assistant with
    # byte-identical tool_calls.
    last_assist = None
    last_assist_idx = None
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.role is Role.assistant and m.tool_calls:
            last_assist = m
            last_assist_idx = i
            break
    if last_assist is None or last_assist_idx is None or last_assist_idx < 2:
        return None

    # The tool result that errored should sit just before last_assist (or
    # immediately before, given tool results come after their call).
    tool_msg = None
    for j in range(last_assist_idx - 1, -1, -1):
        if messages[j].role is Role.tool:
            tool_msg = messages[j]
            break
        if messages[j].role is Role.assistant:
            break  # no tool result between — bail
    if tool_msg is None or not _tool_result_errored(tool_msg):
        return None

    # The assistant turn that called the tool whose result we're examining
    # is above the tool_msg.
    prior_assist = None
    for k in range(messages.index(tool_msg) - 1, -1, -1):
        if messages[k].role is Role.assistant and messages[k].tool_calls:
            prior_assist = messages[k]
            break
    if prior_assist is None:
        return None

    # Compare signatures — fire only if byte-identical call set.
    def sigs(a: LLMMessage) -> list[str]:
        return [_tool_sig(tc) for tc in (a.tool_calls or [])]

    if sigs(last_assist) != sigs(prior_assist):
        return None

    tool_name = (last_assist.tool_calls[0].function.name
                 if last_assist.tool_calls else "?")
    err_snippet = str(getattr(tool_msg, "content", "") or "")[:200]
    return Finding(
        code=f"retry_after_error:{tool_name}:{err_snippet[:60]}",
        directive=(
            f"Admiral: you just called `{tool_name}` with the same arguments "
            f"that errored on the previous turn. Error head: {err_snippet!r}. "
            "Change the arguments, try a different tool, or report what's "
            "blocking you. Do not retry identically."
        ),
    )


# Module-level diagnostic helper for integration tests.
def run_proposed_detectors(messages: Sequence[LLMMessage]) -> list[Finding]:
    """Run only the proposed (not-yet-wired) detectors. For offline evaluation."""
    results: list[Finding] = []
    for fn in (detect_empty_after_tool, detect_retry_after_error):
        f = fn(messages)
        if f is not None:
            results.append(f)
    return results

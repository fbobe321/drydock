"""Heuristic detectors that inspect the live message list.

Each detector is a pure function: take the messages, return a
`Finding(code, directive)` if it fires, or `None`. Findings are
idempotent within a short window — callers dedup before intervening.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from drydock.core.types import LLMMessage, Role


WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "search_replace", "edit_file"})


@dataclass(frozen=True)
class Finding:
    code: str          # stable ID used for dedup, e.g. "loop:write_file:/p/x.py"
    directive: str     # text to inject into the conversation


def _tool_sig(tc) -> str:  # ToolCall — keep loose to avoid circular import
    name = tc.function.name or ""
    args = tc.function.arguments or ""
    return f"{name}::{args}"


def detect_tool_call_loop(messages: Sequence[LLMMessage], window: int = 3) -> Finding | None:
    """Fires when the last `window` assistant tool calls are byte-identical.

    Drydock already has its own loop prevention; this catches the cases
    where the existing system warns-but-doesn't-stop and the model
    ignored the warning.
    """
    sigs: list[str] = []
    for m in reversed(messages):
        if m.role != Role.assistant or not m.tool_calls:
            continue
        for tc in m.tool_calls:
            sigs.append(_tool_sig(tc))
            if len(sigs) >= window:
                break
        if len(sigs) >= window:
            break
    if len(sigs) < window:
        return None
    if len(set(sigs)) != 1:
        return None
    sig = sigs[0]
    return Finding(
        code=f"loop:{sig[:80]}",
        directive=(
            f"Admiral: you have called the same tool with identical arguments "
            f"{window} times in a row. The next call will almost certainly "
            f"produce the same result. Stop this path, try a different "
            f"approach, or tell the user what's blocking you."
        ),
    )


def detect_struggle(messages: Sequence[LLMMessage], threshold: int = 20) -> Finding | None:
    """Fires when `threshold` tool calls have happened without any write.

    Reading + grepping forever without writing code is the classic
    "stuck exploring" failure mode.
    """
    calls_since_write = 0
    last_write_tool: str | None = None
    for m in messages:
        if m.role != Role.assistant or not m.tool_calls:
            continue
        for tc in m.tool_calls:
            name = tc.function.name or ""
            if name in WRITE_TOOLS:
                calls_since_write = 0
                last_write_tool = name
            else:
                calls_since_write += 1
    if calls_since_write < threshold:
        return None
    hint = (
        "Either (a) you already have enough context — commit to a plan and "
        "start writing code now, or (b) you're genuinely stuck — stop and "
        "report what's blocking you to the user."
    )
    return Finding(
        code=f"struggle:{calls_since_write}:{last_write_tool or 'none'}",
        directive=(
            f"Admiral: you have made {calls_since_write} tool calls without "
            f"writing or editing any file. {hint}"
        ),
    )


def run_all(messages: Sequence[LLMMessage]) -> list[Finding]:
    """Run every detector and return all findings that fire."""
    results: list[Finding] = []
    for fn in (detect_tool_call_loop, detect_struggle):
        f = fn(messages)
        if f is not None:
            results.append(f)
    return results

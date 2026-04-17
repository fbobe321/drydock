"""Infer task_type from a session's messages.

Used by the Phase 3a tuning layer to key hyperparameters off of
`(model, task_type)` rather than just model. A coding build needs
different tolerances than an explore/research session.

Heuristic only — cheap to run on every turn. Returns one of:
* `build`    — creating a new project / many writes to fresh files
* `bugfix`   — modifying existing code to fix failures
* `explore`  — reading/querying without writing
* `refactor` — mass rewrites without new features
* `unknown`  — too early to tell, or none of the above
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from drydock.core.types import LLMMessage, Role

_BUILD_HINTS = re.compile(r"\b(build|create|scaffold|make|generate)\b", re.I)
_BUGFIX_HINTS = re.compile(r"\b(fix|bug|error|traceback|failing|broken|crash)\b", re.I)
_EXPLAIN_HINTS = re.compile(r"\b(explain|how does|walk through|describe|understand)\b", re.I)
_REFACTOR_HINTS = re.compile(r"\b(refactor|rename|restructure|clean up|reorganize)\b", re.I)

_WRITE_TOOLS = frozenset({"write_file", "search_replace", "edit_file"})
_READ_TOOLS = frozenset({"read_file", "grep", "glob", "bash"})


def _first_user_prompt(messages: Sequence[LLMMessage]) -> str:
    for m in messages:
        if m.role == Role.user and isinstance(m.content, str):
            return m.content[:500]
    return ""


def classify(messages: Sequence[LLMMessage]) -> str:
    prompt = _first_user_prompt(messages)

    # Quick prompt-keyword pass on the first user message.
    if _EXPLAIN_HINTS.search(prompt):
        return "explore"
    if _REFACTOR_HINTS.search(prompt):
        return "refactor"
    if _BUGFIX_HINTS.search(prompt):
        return "bugfix"
    if _BUILD_HINTS.search(prompt):
        return "build"

    # Tool-mix fallback.
    writes = 0
    reads = 0
    for m in messages:
        if m.role != Role.assistant or not m.tool_calls:
            continue
        for tc in m.tool_calls:
            name = tc.function.name or ""
            if name in _WRITE_TOOLS:
                writes += 1
            elif name in _READ_TOOLS:
                reads += 1
    total = writes + reads
    if total < 4:
        return "unknown"
    if writes >= 5 and writes >= reads:
        return "build"
    if writes >= 1 and reads > writes * 3:
        return "bugfix"
    if reads > 0 and writes == 0:
        return "explore"
    if writes > 0 and reads > 0:
        return "refactor"
    return "unknown"

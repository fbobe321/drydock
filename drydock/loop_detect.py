"""Loop detection — guide, never block.

A local model sometimes repeats the same tool call over and over (re-reading
a file, re-running an identical search, retrying an edit that already
applied). Drydock never hard-stops legitimate work for this: instead it
notices exact repeats and prepends an advisory NOTE to the tool result, with
escalating firmness. The only hard ceiling is the agent loop's max-turns
limit. Loop-breakers always return a result; they never raise.

All logic original to Drydock.
"""
from __future__ import annotations

import json


def tool_signature(name: str, inputs: dict) -> str:
    """Stable signature for an exact (name, inputs) tool call."""
    try:
        payload = json.dumps(inputs, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = repr(inputs)
    return f"{name}\x00{payload}"


def loop_note(name: str, count: int, failed: bool = False) -> str | None:
    """Advisory note for the *count*-th identical call of this tool.

    count == 1  -> None   (first call, nothing to say)
    count >= 2  -> escalating reminders.

    When *failed* is True the repeat returned an error, so "use that result" is
    wrong advice — the model must CHANGE the call (e.g. copy the exact text from
    the error) instead of repeating it. Failed repeats get fix-it phrasing.
    """
    if count <= 1:
        return None
    if failed:
        if count == 2:
            return (
                f"[NOTE: this {name} call FAILED with these exact arguments "
                f"already — repeating it gives the same error. Do NOT repeat it. "
                f"Fix the cause: for an edit, copy the EXACT current text from the "
                f"error message (or a Read) as old_string; otherwise change the "
                f"arguments or use a different tool.]"
            )
        return (
            f"[NOTE: {name} has now FAILED {count} times with identical arguments "
            f"— it will keep failing. STOP repeating it. Copy the exact text from "
            f"the error/Read into your next call, or take a different approach.]"
        )
    if count == 2:
        return (
            f"[NOTE: you have already called {name} with these exact arguments "
            f"once before and got the same result. Use that result instead of "
            f"repeating the call.]"
        )
    if count < 5:
        return (
            f"[NOTE: you have called {name} with identical arguments {count} "
            f"times and the result has not changed. Stop repeating this call. "
            f"Use the result above, or try different arguments / a different "
            f"tool.]"
        )
    return (
        f"[NOTE: {name} has now been called {count} times with the same "
        f"arguments and is clearly looping. Do NOT call it again with these "
        f"arguments. Take a different approach, or stop and summarize what "
        f"you have so the user can guide the next step.]"
    )


def path_thrash_note(path: str, count: int) -> str | None:
    """Advisory for the *count*-th write/edit to the same path this session.

    Distinct from exact-repeat detection: this fires even when the CONTENT
    differs each time — the signal that the model is thrashing one file instead
    of making one correct change. Quiet until the 3rd write to avoid noise.
    """
    if count < 3:
        return None
    return (
        f"[NOTE: this is write #{count} to {path} this session. Repeatedly "
        f"rewriting the same file usually means a wrong approach — re-read the "
        f"current file, make ONE correct change, then move on.]"
    )


class LoopTracker:
    """Counts identical tool calls across a run and produces advisory notes."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._path_writes: dict[str, int] = {}

    def record(self, name: str, inputs: dict) -> int:
        """Record a call; return how many times this exact call has occurred."""
        sig = tool_signature(name, inputs)
        self._counts[sig] = self._counts.get(sig, 0) + 1
        return self._counts[sig]

    def record_path_write(self, name: str, inputs: dict) -> str | None:
        """Track Write/Edit per target path and return a thrash note if the
        same path has now been written 3+ times (regardless of content)."""
        if name not in ("Write", "Edit"):
            return None
        path = inputs.get("file_path")
        if not path:
            return None
        self._path_writes[path] = self._path_writes.get(path, 0) + 1
        return path_thrash_note(path, self._path_writes[path])

    def annotate(self, name: str, inputs: dict, result: str) -> str:
        """Record the call and prepend advisory note(s) to its result for an
        exact repeat and/or same-path write thrash. Never raises.
        """
        count = self.record(name, inputs)
        failed = (result or "").lstrip().startswith(("Error", "REFUSED"))
        note = loop_note(name, count, failed=failed)
        path_note = self.record_path_write(name, inputs)
        # Prune the BODY of a repeated *successful* call (3rd+ identical): the
        # model already has this content, and re-feeding it both wastes context
        # and lets it mindlessly re-call. Replace the body with a stub so the
        # repeat yields nothing new. Failures keep their text (it's the fix).
        if note and not failed and count >= 3:
            result = (
                "(identical to your earlier call to this tool — body omitted. "
                "Re-running it returns nothing new; act on the content you "
                "already have, or take a different step.)"
            )
        prefix = "\n".join(n for n in (note, path_note) if n)
        return f"{prefix}\n{result}" if prefix else result

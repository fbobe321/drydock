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


def loop_note(name: str, count: int) -> str | None:
    """Advisory note for the *count*-th identical call of this tool.

    count == 1  -> None   (first call, nothing to say)
    count == 2  -> gentle reminder
    count 3-4   -> firm: stop repeating, use the result you have
    count >= 5  -> strong: this tool is going in circles, change approach
    """
    if count <= 1:
        return None
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


class LoopTracker:
    """Counts identical tool calls across a run and produces advisory notes."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def record(self, name: str, inputs: dict) -> int:
        """Record a call; return how many times this exact call has occurred."""
        sig = tool_signature(name, inputs)
        self._counts[sig] = self._counts.get(sig, 0) + 1
        return self._counts[sig]

    def annotate(self, name: str, inputs: dict, result: str) -> str:
        """Record the call and prepend an advisory note to its result if it
        is a repeat. Returns the (possibly annotated) result — never raises.
        """
        count = self.record(name, inputs)
        note = loop_note(name, count)
        return f"{note}\n{result}" if note else result

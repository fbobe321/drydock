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


def runaway_repetition_len(
    text: str,
    *,
    window: int = 2000,
    min_run: int = 600,
    max_unit: int = 80,
    min_reps: int = 6,
) -> int:
    """Length (in chars) of a runaway repeated SUFFIX of *text*, else 0.

    A weak local model sometimes collapses mid-generation into emitting the same
    short unit hundreds of times (observed: gemma4 streamed ``295:`` ~1365× on
    make-mips-interpreter). The tool-call loop guard never sees this — it's
    streamed assistant TEXT, not a tool call — so the turn balloons unchecked.

    This detects that one failure mode and nothing else. It is deliberately
    CONSERVATIVE: it requires a long pure-repetition run (>= ``min_run`` chars
    AND >= ``min_reps`` repeats of one <= ``max_unit``-char unit) so that
    legitimate repetition — a ``---`` rule, a short bulleted list, an ASCII
    table — never trips it. Pure-whitespace units (the blank line between
    markdown blocks) are ignored. Returns the run length so the caller can trim
    exactly the repeated tail; 0 means "looks fine, keep going".
    """
    if len(text) < min_run:
        return 0
    tail = text[-window:]
    n = len(tail)
    best = 0
    for p in range(1, min(max_unit, n // min_reps) + 1):
        unit = tail[-p:]
        if not unit.strip():
            continue  # ignore blank-line / whitespace units
        reps = 0
        i = n
        while i - p >= 0 and tail[i - p:i] == unit:
            reps += 1
            i -= p
        run = reps * p
        if reps >= min_reps and run >= min_run:
            best = max(best, run)
    return best


class LoopTracker:
    """Counts identical tool calls across a run and produces advisory notes."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._path_writes: dict[str, int] = {}
        self._outcome_counts: dict[str, int] = {}  # (call, result-body) -> times seen

    def record(self, name: str, inputs: dict) -> int:
        """Record a call; return how many times this exact call has occurred."""
        sig = tool_signature(name, inputs)
        self._counts[sig] = self._counts.get(sig, 0) + 1
        return self._counts[sig]

    def count(self, name: str, inputs: dict) -> int:
        """How many times this exact call has occurred so far, WITHOUT recording a
        new one. Lets progress evaluation read the exact-repeat count after
        annotate() has already recorded the call."""
        return self._counts.get(tool_signature(name, inputs), 0)

    def outcome_count(self, name: str, inputs: dict, result: str) -> int:
        """How many times this exact (call, result) pair has been seen so far,
        WITHOUT recording. Non-consecutive repeats count — the signal that a call
        is cycling (an identical Write re-issued between other actions) even when
        it never repeats back-to-back. Polling is naturally exempt: its result
        changes, so each poll is a different pair."""
        key = f"{tool_signature(name, inputs)}\x00{hash(result or '')}"
        return self._outcome_counts.get(key, 0)

    def record_outcome(self, name: str, inputs: dict, result: str) -> int:
        """Record that this exact call produced this exact result body; return how
        many times that (call, outcome) pair has now been seen. A repeated-OUTCOME
        counter (PRD Epic J3): distinct from the call counter because it keys on
        the result too, so it only climbs when the call keeps producing the SAME
        answer — the signal that re-running is pointless."""
        key = f"{tool_signature(name, inputs)}\x00{hash(result or '')}"
        self._outcome_counts[key] = self._outcome_counts.get(key, 0) + 1
        return self._outcome_counts[key]

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
        outcome_count = self.record_outcome(name, inputs, result)
        # Outcome-aware repeat handling: a repeated call whose RESULT is also
        # repeating is a loop; a repeated call whose result CHANGES is polling —
        # legit, and must get neither notes nor pruning (pruning it to a constant
        # stub both loses the fresh output AND makes the poll look like a loop
        # downstream). Failed repeats keep their fix-it notes on call identity
        # alone (the error text is the fix, sameness of body not required).
        if failed:
            note = loop_note(name, count, failed=True)
        else:
            note = loop_note(name, outcome_count, failed=False)
        path_note = self.record_path_write(name, inputs)
        # Prune the BODY of a repeated *successful, unchanged* outcome (3rd+ time
        # this exact call returned this exact body): the model already has this
        # content, and re-feeding it both wastes context and lets it mindlessly
        # re-call. Replace the body with a stub so the repeat yields nothing new.
        if not failed and outcome_count >= 3:
            result = (
                "(identical to your earlier call to this tool — body omitted. "
                "Re-running it returns nothing new; act on the content you "
                "already have, or take a different step.)"
            )
        # Prune the BODY of a repeated *failing* outcome (3rd+ time this exact
        # call produced this exact error). The first couple of failures carry the
        # fix (e.g. the current text to copy into an edit), so they keep their
        # text — but once the SAME error has repeated, re-feeding it just bloats
        # context and reinforces the loop (observed: one failing test rerun 55×
        # with a byte-identical 564-char error). Keys on the result body, so a
        # call whose error is actually CHANGING (real progress) is never pruned.
        elif failed and outcome_count >= 3:
            result = (
                f"(identical error to {outcome_count - 1} earlier attempts — this "
                f"exact call keeps producing the SAME failure, so re-running it "
                f"changes nothing. Stop repeating it; change the approach or take a "
                f"different step.)"
            )
        prefix = "\n".join(n for n in (note, path_note) if n)
        return f"{prefix}\n{result}" if prefix else result

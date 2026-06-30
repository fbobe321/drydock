"""Core agent loop — multi-turn tool-calling with any LLM.

DryDock v3 — clean, provider-agnostic, no model-specific hacks.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Generator

# Max CONSECUTIVE text-only stalls (counter resets on any real tool call) we
# nudge through when the model leaves its own plan unfinished. Bounds a true
# stall-loop while letting a long, productive plan run as far as it needs.
PLAN_CONTINUE_CAP = 3


def _plan_has_unfinished(config: dict) -> bool:
    """True if the model laid out a `todo` plan that still has non-done items."""
    todo = config.get("_todo")
    return bool(todo) and any(status != "done" for _, status in todo)

from drydock.providers import stream, AssistantTurn, ReasoningChunk, TextChunk
from drydock.tool_registry import schemas, execute
from drydock.tools import register_all

# Register the built-in tools as a side effect of importing the agent. This is
# explicit (not a bare side-effect import) so a linter can't "helpfully" delete
# it: without it the registry is empty, the model is offered no tools, and it
# emits tool calls as TEXT — which is exactly how the empty-registry regression
# manifested. register_all() is idempotent.
register_all()
from drydock.compaction import (
    maybe_compact, emergency_compact, is_context_length_error, is_image_load_error,
)
from drydock.loop_detect import LoopTracker
from drydock.tuning import (
    filter_tool_schemas,
    hallucinated_tool_message,
    thinking_level_for_turn,
)


# ── Event types ───────────────────────────────────────────────────────────

@dataclass
class ToolStart:
    name: str
    inputs: dict

@dataclass
class ToolEnd:
    name: str
    result: str

@dataclass
class TurnDone:
    input_tokens: int
    output_tokens: int

@dataclass
class AgentState:
    """Mutable session state."""
    messages: list = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    turn_count: int = 0
    current_effort: str = ""  # "high"/"low" of the in-flight LLM call (for the UI)


def drop_last_turn(messages: list) -> bool:
    """Remove the last user message and everything after it (the assistant
    replies + tool results for that turn). Returns True if a turn was dropped.

    Cutting at a user-message boundary keeps the history valid — the remaining
    list ends with a complete prior turn and never leaves an orphaned tool
    result (those all followed the removed user message).
    """
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            del messages[i:]
            return True
    return False


# ── Agent loop ────────────────────────────────────────────────────────────

def run(
    user_message: str,
    state: AgentState,
    config: dict,
    system_prompt: str,
) -> Generator:
    """Multi-turn agent loop.

    Yields: TextChunk | ToolStart | ToolEnd | TurnDone

    The loop continues as long as the model makes tool calls.
    When the model responds with text only (no tools), the turn ends.
    """
    state.messages.append({"role": "user", "content": user_message})

    max_turns = config.get("max_turns", 200)
    max_tool_calls = config.get("max_tool_calls", 0)  # 0 = unlimited
    # When set (sub-agents pass this), the model is offered ONLY these tools and
    # a call to anything else is refused — never executed. Keeps a read-only
    # sub-agent read-only and stops it from recursing into `task`.
    allow = config.get("tool_allowlist")
    # User STOP signal (a threading.Event the TUI sets on Escape / "/stop").
    # Checked only at SAFE points — top of the loop and after a turn's tool
    # results are all appended — so a stop never leaves an assistant tool_call
    # without its matching tool result (which would corrupt the history).
    cancel = config.get("_cancel")
    def _stopped() -> bool:
        return cancel is not None and cancel.is_set()
    tool_call_count = 0
    session_has_edited = False
    leaked_call_retries = 0
    plan_continue_nudges = 0  # consecutive "you stopped mid-plan" nudges
    empty_response_nudges = 0  # consecutive "you returned nothing" nudges
    # Safety valve for a degenerate loop: the SAME tool call run over and over
    # with the SAME result — success OR failure (seen failing a Write 160×, and
    # re-running an identical passing `pytest` 92× for 25 min). The advisory
    # loop-note is ignored by weak models, so after a cap we end the turn. Real
    # iterative fixing changes the args, and polling yields a changing result —
    # both reset the streak, so only a truly pointless loop trips it.
    identical_repeat_streak = 0
    last_call_sig = None
    IDENTICAL_REPEAT_CAP = 8
    run_iteration = 0  # stream calls within THIS run() (resets per user message)
    loop_tracker = LoopTracker()

    while state.turn_count < max_turns:
        if _stopped():
            break
        state.turn_count += 1
        run_iteration += 1
        assistant_turn: AssistantTurn | None = None

        # Compact context if approaching limit
        maybe_compact(state, config)

        # Force tool use on first turn to prevent the model from just outputting text
        turn_config = dict(config)
        if state.turn_count == 1 and config.get("force_first_tool", False):
            turn_config["tool_choice"] = "required"
        # Adaptive reasoning budget: high to PLAN the response to the user's new
        # message (first iteration of this run), low for routine continuation
        # turns that just consume tool results — cuts latency without hurting
        # correctness (verified: same answer, fewer tokens). The provider only
        # forwards it to endpoints that accept reasoning_effort.
        if "reasoning_effort" not in turn_config:
            level = thinking_level_for_turn(run_iteration, is_user_turn=(run_iteration == 1))
            turn_config["reasoning_effort"] = "high" if level == "high" else "low"
        # Expose the in-flight effort to the UI (GIL-atomic string read in the
        # status line; no message plumbing needed).
        state.current_effort = turn_config.get("reasoning_effort", "")
        # After many calls without editing, don't force but the nudge message handles it

        # Stream from LLM — with retry on context-length 400 error
        retries = 0
        while retries < 2:
            try:
                available = schemas()
                if allow is not None:
                    available = [s for s in available if s.get("name") in allow]
                for event in stream(
                    model=turn_config["model"],
                    system=system_prompt,
                    messages=state.messages,
                    tool_schemas=filter_tool_schemas(available, turn_config.get("model")),
                    config=turn_config,
                ):
                    if isinstance(event, ReasoningChunk):
                        yield event
                    elif isinstance(event, TextChunk):
                        yield event
                    elif isinstance(event, AssistantTurn):
                        assistant_turn = event
                break  # success
            except Exception as e:
                err = str(e)
                if is_context_length_error(err):
                    retries += 1
                    limit = config.get("context_limit", 131072)
                    state.messages = emergency_compact(state.messages, limit)
                    if retries >= 2:
                        raise
                    yield TextChunk("\n[context limit hit — compacting and retrying...]\n")
                elif is_image_load_error(err):
                    # Corrupt/truncated/unsupported image the server couldn't decode —
                    # end the turn cleanly rather than dumping the raw 400.
                    yield TextChunk(
                        "\n[The model server couldn't load an attached image — it may be "
                        "corrupt, truncated, or an unsupported format. Try a different image.]\n"
                    )
                    assistant_turn = None
                    break
                else:
                    raise

        if assistant_turn is None:
            break

        # Record assistant message
        state.messages.append({
            "role": "assistant",
            "content": assistant_turn.text,
            "tool_calls": assistant_turn.tool_calls,
        })

        state.total_input_tokens += assistant_turn.input_tokens
        state.total_output_tokens += assistant_turn.output_tokens
        yield TurnDone(assistant_turn.input_tokens, assistant_turn.output_tokens)

        # No tool calls = conversation complete — UNLESS the model emitted a
        # tool call as text (a Gemma quirk the API can't structure). In that
        # case nudge it to use the real function interface and retry, instead
        # of ending the turn with nothing done. Capped so it can never spin.
        if not assistant_turn.tool_calls:
            if assistant_turn.had_leaked_call and leaked_call_retries < 2:
                leaked_call_retries += 1
                state.messages.append({
                    "role": "user",
                    "content": (
                        "[SYSTEM] Your tool call came through as plain text, so "
                        "it did not run. Call the tool using the function/tool "
                        "interface — not text, no <|tool_call> markers. Use the "
                        "exact tool names (Write, Read, Edit, Bash). Try again."
                    ),
                })
                continue
            # Don't stall mid-plan: if the model laid out a todo and still has
            # unfinished steps, nudge it to keep going instead of waiting for
            # the user to say "proceed". Capped (consecutive) + env-gated so it
            # can never wedge; resets to 0 on any real tool call below.
            if (
                _plan_has_unfinished(config)
                and plan_continue_nudges < PLAN_CONTINUE_CAP
                and not os.environ.get("DRYDOCK_PLAN_AUTOCONTINUE_DISABLE")
            ):
                plan_continue_nudges += 1
                state.messages.append({
                    "role": "user",
                    "content": (
                        "[SYSTEM] Your plan still has unfinished steps. Keep "
                        "going now — do the next step with a tool call; do NOT "
                        "stop to ask whether to proceed. If every step is truly "
                        "done, call `todo` once more marking them all [x]."
                    ),
                })
                continue
            # A completely EMPTY response (no text, no tool call, no leaked call)
            # is a non-answer, not a deliberate "done" — a weak model sometimes
            # returns nothing on a hard task and the turn dead-ends silently.
            # Nudge ONCE to produce output. Narrow (only empty text) and capped,
            # so unlike a blanket auto-continue it can't loop on real answers.
            if not (assistant_turn.text or "").strip() and empty_response_nudges < 1:
                empty_response_nudges += 1
                state.messages.append({
                    "role": "user",
                    "content": (
                        "[SYSTEM] Your last response was empty. Produce your "
                        "result now: either call a tool to do the work, or reply "
                        "with text. Do not return an empty message."
                    ),
                })
                continue
            break

        # Execute each tool call
        for tc in assistant_turn.tool_calls:
            tool_call_count += 1
            if tc["name"] in ("Edit", "Write"):
                session_has_edited = True

            # STOP pressed: don't run the remaining tools, but still record a
            # paired result for each (the assistant message already lists all
            # tool_calls — leaving one without a tool result corrupts history).
            if _stopped():
                state.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["name"],
                    "content": "[skipped — stopped by user]",
                })
                continue

            # Check tool call limit
            if max_tool_calls > 0 and tool_call_count > max_tool_calls:
                state.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["name"],
                    "content": "[Tool call limit reached. Respond with your final answer now.]",
                })
                continue

            yield ToolStart(tc["name"], tc["input"])

            # Redirect hallucinated tool names to a benign hint instead of a
            # "tool not found" error the model would loop on.
            halluc = hallucinated_tool_message(tc["name"])
            if halluc is not None:
                result = halluc
            elif allow is not None and tc["name"] not in allow:
                result = (
                    f"[The '{tc['name']}' tool is not available here. You may use "
                    f"only: {', '.join(allow)}. Use one of those, or reply with "
                    "your final summary.]"
                )
            else:
                result = execute(tc["name"], tc["input"], config)
            # Track consecutive byte-identical calls — same name, args AND raw
            # result (captured before annotate prepends its note, which changes
            # each call) — for the safety valve below. A differing result
            # (polling) or differing args (real iteration) resets the streak.
            sig = (tc["name"], str(tc["input"]), result)
            if sig == last_call_sig:
                identical_repeat_streak += 1
            else:
                identical_repeat_streak, last_call_sig = 1, sig
            # Guide (never block) on exact-repeat tool calls: prepend an
            # advisory note when the same call is made again.
            result = loop_tracker.annotate(tc["name"], tc["input"], result)

            yield ToolEnd(tc["name"], result)

            # Append tool result
            state.messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": tc["name"],
                "content": result,
            })

        # Safety valve: the same call has run identically (same args AND result)
        # too many times in a row — repeating it changes nothing. End the turn
        # and hand control back rather than burning turns toward MAX_TOOL_TURNS.
        if identical_repeat_streak >= IDENTICAL_REPEAT_CAP and last_call_sig:
            yield TextChunk(
                f"\n[Stopped: the same {last_call_sig[0]} call ran "
                f"{identical_repeat_streak}× in a row with the same result — "
                "repeating it changes nothing. Control is back to you; tell me "
                "how you'd like to proceed.]\n"
            )
            break

        # Safe point (all tool results appended): honor a STOP requested while
        # this turn's tools were running, before spending another LLM call.
        if _stopped():
            break

        # Real progress this turn — reset the consecutive stall-nudge counter so
        # a long, productive plan can run as far as it needs (the cap only
        # bounds back-to-back stalls).
        plan_continue_nudges = 0
        empty_response_nudges = 0

        # Nudge: if past 15 tool calls without any edits, inject gentle guidance
        if tool_call_count == 15 and not session_has_edited and config.get("force_first_tool"):
            state.messages.append({
                "role": "user",
                "content": "[SYSTEM] Reminder: you should call Edit to fix the bug soon. "
                           "Read the file if you haven't already, then make your edit.",
            })

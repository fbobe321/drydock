"""Core agent loop — multi-turn tool-calling with any LLM.

DryDock v3 — clean, provider-agnostic, no model-specific hacks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generator

from drydock.providers import stream, AssistantTurn, TextChunk
from drydock.tool_registry import schemas, execute
from drydock.tools import register_all

# Register the built-in tools as a side effect of importing the agent. This is
# explicit (not a bare side-effect import) so a linter can't "helpfully" delete
# it: without it the registry is empty, the model is offered no tools, and it
# emits tool calls as TEXT — which is exactly how the empty-registry regression
# manifested. register_all() is idempotent.
register_all()
from drydock.compaction import maybe_compact, emergency_compact, is_context_length_error
from drydock.loop_detect import LoopTracker
from drydock.tuning import filter_tool_schemas


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
    tool_call_count = 0
    session_has_edited = False
    leaked_call_retries = 0
    loop_tracker = LoopTracker()

    while state.turn_count < max_turns:
        state.turn_count += 1
        assistant_turn: AssistantTurn | None = None

        # Compact context if approaching limit
        maybe_compact(state, config)

        # Force tool use on first turn to prevent the model from just outputting text
        turn_config = dict(config)
        if state.turn_count == 1 and config.get("force_first_tool", False):
            turn_config["tool_choice"] = "required"
        # After many calls without editing, don't force but the nudge message handles it

        # Stream from LLM — with retry on context-length 400 error
        retries = 0
        while retries < 2:
            try:
                for event in stream(
                    model=turn_config["model"],
                    system=system_prompt,
                    messages=state.messages,
                    tool_schemas=filter_tool_schemas(schemas(), turn_config.get("model")),
                    config=turn_config,
                ):
                    if isinstance(event, TextChunk):
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
            break

        # Execute each tool call
        for tc in assistant_turn.tool_calls:
            tool_call_count += 1
            if tc["name"] in ("Edit", "Write"):
                session_has_edited = True

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

            result = execute(tc["name"], tc["input"], config)
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

        # Nudge: if past 15 tool calls without any edits, inject gentle guidance
        if tool_call_count == 15 and not session_has_edited and config.get("force_first_tool"):
            state.messages.append({
                "role": "user",
                "content": "[SYSTEM] Reminder: you should call Edit to fix the bug soon. "
                           "Read the file if you haven't already, then make your edit.",
            })

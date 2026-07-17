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

# Over-think interrupt: when a call keeps stalling/over-thinking (repeated
# StallRetry), re-issue in "decisive mode" — a forcing suffix + a hard token cap
# so the model physically cannot burn thousands of reasoning tokens without
# acting. The model that produced this loop (gemma) generates 5-15 min / 5k+
# token "thinking" turns that stall_retry alone can't break (tokens flow); this
# forces a short, single-action turn instead.
_DECISIVE_MAX_TOKENS = 1500
_DECISIVE_SUFFIX = (
    "\n\nURGENT — you have spent too long reasoning without acting. STOP "
    "analyzing. Your reply must be SHORT and take exactly ONE concrete action "
    "right now: emit a single tool call (run a command or write a file) with "
    "little or no explanation. Do NOT lay out a plan; act."
)


def _plan_has_unfinished(config: dict) -> bool:
    """True if the model laid out a `todo` plan that still has non-done items."""
    todo = config.get("_todo")
    return bool(todo) and any(status != "done" for _, status in todo)

from drydock.providers import stream, AssistantTurn, ReasoningChunk, TextChunk, StallRetry, RepetitionDetected
from drydock.tool_registry import schemas, execute_structured
from drydock.tools import register_all

# Register the built-in tools as a side effect of importing the agent. This is
# explicit (not a bare side-effect import) so a linter can't "helpfully" delete
# it: without it the registry is empty, the model is offered no tools, and it
# emits tool calls as TEXT — which is exactly how the empty-registry regression
# manifested. register_all() is idempotent.
register_all()
from drydock.compaction import (
    maybe_compact, emergency_compact, is_context_length_error, is_image_load_error,
    extract_server_n_ctx,
)
from drydock.loop_detect import LoopTracker
from drydock.task_state import TaskState
from drydock.verification import (
    looks_like_verification,
    parse_evidence,
    extract_artifacts,
    uncovered_artifacts,
)
from drydock.progress import ProgressTracker, assess_action, NO_PROGRESS_WINDOW
from drydock.recovery import RecoveryController, SUPPRESSION_ITERATIONS
from drydock.loop_detect import tool_signature
from drydock.phases import AgentPhase, PhaseController
from drydock.budget import BudgetState
from drydock.tool_select import select_tools, DEFAULT_MAX_TOOLS
from drydock.tool_registry import get as tool_get, canonical_name
from drydock.tool_validate import repair_and_validate, INVALID_ARGUMENTS
from drydock.tool_policy import (
    requires_approval as tool_requires_approval,
    effect_of as tool_effect_of,
)
from drydock.events import EventLog, SQLiteEventLog, emit as _emit
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
    last_input_tokens: int = 0  # prompt tokens the SERVER counted on the last call
    turn_count: int = 0
    current_effort: str = ""  # "high"/"low" of the in-flight LLM call (for the UI)
    task: "TaskState" = field(default_factory=lambda: TaskState())  # structured objective
    events: "EventLog | SQLiteEventLog | None" = None  # optional durable execution trace
    recovery_stage: int = 0  # live recovery escalation stage (0 = normal); for the TUI
    progress_streak: int = 0  # consecutive no-progress (stall) actions; for the TUI
    budget: "BudgetState" = field(default_factory=lambda: BudgetState())  # scoped budgets


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
    state.recovery_stage = 0  # clear the governor's display for a fresh request
    state.progress_streak = 0

    # Capture the structured objective the FIRST time a task arrives, so it lives
    # outside the transcript. The original objective is authoritative for the task.
    if not state.task.is_set() and isinstance(user_message, str) and user_message.strip():
        state.task = TaskState.from_objective(user_message)
        _emit(state, "task_start", objective=state.task.objective,
              acceptance_criteria=state.task.acceptance_criteria)
    _emit(state, "user_message", chars=len(user_message or ""))
    # Keep the objective + acceptance criteria in the SYSTEM PROMPT every turn, so
    # they survive compaction (which only touches the message transcript, never the
    # system prompt) — the model can't drift off the goal on a long task.
    if config.get("task_anchor", True) and state.task.is_set():
        anchor = state.task.anchor_text()
        if anchor:
            system_prompt = system_prompt + "\n\n" + anchor

    # Recipe retrieval: give the model the TECHNIQUE this task needs (forensics,
    # git-history rewrite, numpy-2.0 fix, cert gen, …) by appending the relevant
    # bundled recipes to the system prompt. Keyword-matched, so only fitting ones
    # are added; none matched = no change. Gated by config `recipes`.
    if config.get("recipes", True):
        from drydock.recipes import recipe_context
        extra = recipe_context(user_message)
        if extra:
            system_prompt = system_prompt + extra

    # Scoped budgets (PRD Epic N): the loop bounds itself on a PER-REQUEST
    # iteration budget that resets each user message (so a long session doesn't
    # starve later requests), while session totals stay cumulative. Limits come
    # from config; the BudgetState persists on the AgentState across requests.
    budget = state.budget
    budget.max_model_iterations = config.get("max_turns", 200)
    budget.max_tool_calls = config.get("max_tool_calls", 0)  # 0 = unlimited
    budget.max_recovery_attempts = config.get("max_recovery_attempts", 0)
    budget.start_request()
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
    last_verification = None    # VerificationEvidence of the most recent check (gate)
    verify_gate_nudges = 0      # bounded "verify before you finish" nudges
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
    # Recovery tuning (PRD §13) is configurable; defaults match the PRD.
    progress_tracker = ProgressTracker(
        window=config.get("recovery_no_progress_window", NO_PROGRESS_WINDOW),
    )  # scores each action; flags a stalled run
    prev_failing = None  # failing-test count from the previous verification (delta)
    verif_fail_counts: dict[str, int] = {}  # failure-summary -> times seen (Epic J3)
    verification_text = ""    # concatenated verification commands+results (coverage)
    coverage_nudges = 0       # bounded "your checks never touched X" nudges
    recovery = RecoveryController(
        suppression_iterations=config.get("recovery_suppression_iterations",
                                          SUPPRESSION_ITERATIONS),
    )  # escalates when progress stalls (PRD Epic K)
    recovery_terminate = False  # set by stage-5 recovery to end the run honestly
    phases = PhaseController()  # owns phase transitions (PRD Epic B)

    while not budget.iterations_exhausted():
        if _stopped():
            break
        state.turn_count += 1     # cumulative session counter (telemetry / status line)
        budget.record_turn()      # per-request iteration budget (PRD Epic N)
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
        stall_retries = 0
        decisive = False  # over-think interrupt: force a short, single-action turn
        while retries < 2:
            try:
                available = schemas()
                if allow is not None:
                    available = [s for s in available if s.get("name") in allow]
                # Dynamic tool selection (PRD Epic F): expose only the tools
                # relevant to this task + phase, capped at max_tools (default 12).
                # Trims nothing when already under the cap; core coding tools are
                # never dropped.
                available = select_tools(
                    available,
                    phase=str(state.task.phase),
                    task_text=state.task.objective,
                    max_tools=turn_config.get("max_tools", DEFAULT_MAX_TOOLS),
                )
                for event in stream(
                    model=turn_config["model"],
                    system=(system_prompt + _DECISIVE_SUFFIX) if decisive else system_prompt,
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
            except StallRetry as _sr:
                # Stalled/over-thought past stall_retry_secs (wall-time), OR collapsed
                # into a pure-repetition loop (RepetitionDetected, content-based). A
                # first wall-time retry re-issues as-is (a transient hang clears); a
                # persistent stall OR any repetition loop goes straight to decisive
                # mode — a forcing suffix + hard token cap that make a long reasoning
                # turn impossible. Repetition escalates immediately (re-issuing a loop
                # just loops again). Bounded; on exhaustion end cleanly.
                is_rep = isinstance(_sr, RepetitionDetected)
                stall_retries += 1
                if stall_retries > 3:
                    yield TextChunk("\n[model kept stalling/looping — giving up on this step.]\n")
                    assistant_turn = None
                    break
                if (is_rep or stall_retries >= 2) and not decisive:
                    decisive = True
                    turn_config = dict(turn_config)
                    cur = int(turn_config.get("max_tokens", 8192) or 8192)
                    turn_config["max_tokens"] = min(cur, _DECISIVE_MAX_TOKENS)
                    turn_config["reasoning_effort"] = "low"
                    why = "output looping" if is_rep else "taking too long"
                    yield TextChunk(f"\n[{why} — forcing a decisive, single-action step...]\n")
                else:
                    yield TextChunk("\n[model server stalled — retrying...]\n")
                continue
            except Exception as e:
                err = str(e)
                if is_context_length_error(err):
                    retries += 1
                    limit = config.get("context_limit", 131072)
                    # The server told us its real n_ctx (llama.cpp 400 body includes
                    # 'n_ctx': <N>). ADOPT it as the runtime context_limit when it's
                    # smaller than the configured value — so it self-heals: from now
                    # on the ctx gauge, PROACTIVE compaction (maybe_compact at ~60%),
                    # and this emergency path all target the REAL window instead of a
                    # too-high config. Without this, a user with context_limit=64K on a
                    # 32K server re-hits the wall every long turn (GitHub issue #25).
                    server_ctx = extract_server_n_ctx(err)
                    if server_ctx and server_ctx < limit:
                        limit = server_ctx
                        if server_ctx < config.get("context_limit", limit):
                            config["context_limit"] = server_ctx
                            yield TextChunk(
                                f"\n[Your model server reports only {server_ctx:,} tokens available "
                                "PER REQUEST (n_ctx). If the server's TOTAL context is larger, this "
                                "is usually PARALLEL SLOTS dividing it — llama.cpp splits -c across "
                                "-np slots (e.g. 262144 / 8 = 32768 each). For the full window per "
                                "request, restart the server with -np 1. drydock will use "
                                f"{server_ctx:,} for now.]\n")
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
        state.last_input_tokens = assistant_turn.input_tokens
        yield TurnDone(assistant_turn.input_tokens, assistant_turn.output_tokens)
        _emit(state, "turn", in_tok=assistant_turn.input_tokens,
              out_tok=assistant_turn.output_tokens,
              tool_calls=len(assistant_turn.tool_calls or []))

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
            # VERIFICATION GATE (PRD Epic B): a text-only "done" is not evidence.
            # If the agent CHANGED files but never ran a test/check/its own code,
            # don't accept completion — make it verify first. Bounded so it can't
            # wedge; once it runs any check (ran_verification) the gate is satisfied.
            # A text-only "done" after editing is accepted only when a check has
            # PASSED. Never ran one → nudge to VERIFY. Ran one that FAILED → the work
            # isn't done → nudge to REPAIR. Bounded so it can't wedge.
            _needs_gate = (
                config.get("verify_gate", True)
                and allow is None            # main task only — not scoped sub-agents
                and session_has_edited
                and verify_gate_nudges < 2
                and (last_verification is None or last_verification.status == "fail")
            )
            if _needs_gate:
                verify_gate_nudges += 1
                if last_verification is None:
                    state.task.phase = phases.advance(state.task.phase, AgentPhase.VERIFY)
                    _emit(state, "verify_gate", kind="unverified", nudge=verify_gate_nudges)
                    msg = (
                        "[SYSTEM] You changed files but have not VERIFIED the work. "
                        "Run a concrete check now: the task's own test/eval/build "
                        "(pytest, test.sh, make, npm test, …) or run the code you "
                        "produced, and confirm it meets EVERY requirement."
                    )
                else:  # a check ran and FAILED
                    state.task.phase = phases.advance(state.task.phase, AgentPhase.REPAIR)
                    _emit(state, "verify_gate", kind="failed", nudge=verify_gate_nudges,
                          summary=last_verification.summary)
                    msg = (
                        f"[SYSTEM] Your verification FAILED ({last_verification.summary}). "
                        "The task is NOT complete. Read the failure, fix the cause, then "
                        "re-run the SAME check until it passes."
                    )
                state.messages.append({"role": "user", "content": msg})
                continue
            if session_has_edited and last_verification and last_verification.status == "pass":
                # Criteria coverage (bench finding: exit-0 self-checks that never
                # touch the files the task is judged on). If the objective names
                # concrete artifacts and NONE of this session's verifications ever
                # referenced some of them, nudge ONCE with exactly what's missing.
                # Advisory + bounded: a second completion claim is accepted.
                _artifacts = extract_artifacts(
                    state.task.objective + "\n" + "\n".join(state.task.acceptance_criteria))
                _missing = uncovered_artifacts(_artifacts, verification_text)
                if _missing and coverage_nudges < 1:
                    coverage_nudges += 1
                    _emit(state, "verify_gate", kind="uncovered", missing=_missing[:6])
                    state.messages.append({"role": "user", "content": (
                        "[SYSTEM] Your check passed, but no verification this session "
                        f"has touched: {', '.join(_missing[:6])} — and the task will be "
                        "judged on them. Run a check that directly validates the stated "
                        "requirement(s) against those files (diff/compare/execute as "
                        "appropriate), then finish."
                    )})
                    continue
                # A check ran and PASSED — verification evidence exists, so the
                # controller approves completion (from VERIFY, its only legal
                # predecessor). This is the one path to COMPLETE.
                state.task.phase = phases.advance(
                    AgentPhase.VERIFY, AgentPhase.COMPLETE, verified=True)
            _emit(state, "done", phase=state.task.phase, edited=session_has_edited,
                  verified=bool(last_verification and last_verification.status == "pass"))
            if config.get("trajectory_file"):
                from drydock import trajectory
                trajectory.record(system_prompt, state, config)
            # Task finished cleanly — drop the resume snapshot so a leftover file
            # always means "interrupted" (PRD P2.1).
            if config.get("resume_path"):
                from drydock import resume as _resume
                _resume.clear_snapshot(config["resume_path"])
            break

        # Execute each tool call
        for tc in assistant_turn.tool_calls:
            tool_call_count += 1
            budget.record_tool_call()  # task-scoped tool-call budget (PRD Epic N)
            if tc["name"] in ("Edit", "Write"):
                if not session_has_edited and state.task.phase in ("understand", "discover", "plan"):
                    state.task.phase = phases.advance(
                        state.task.phase, AgentPhase.IMPLEMENT)  # first change → building
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

            # Check tool call limit (task budget)
            if budget.tool_budget_exhausted():
                state.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["name"],
                    "content": "[Tool call limit reached. Respond with your final answer now.]",
                })
                continue

            yield ToolStart(tc["name"], tc["input"])

            # Recovery stage-3 suppression (PRD Epic K): if this exact call was
            # flagged as a no-progress loop, redirect it to a guidance string
            # instead of running it again — same advisory-not-blocking pattern as
            # hallucinated tools. Narrow (this signature only) and self-expiring.
            recovery.tick(run_iteration)
            recovery_sig = tool_signature(tc["name"], tc["input"])

            # Redirect hallucinated tool names to a benign hint instead of a
            # "tool not found" error the model would loop on.
            halluc = hallucinated_tool_message(tc["name"])
            if recovery.is_suppressed(recovery_sig):
                result = recovery.suppression_message(tc["name"])
                tool_result = None
            elif halluc is not None:
                result = halluc
                tool_result = None
            elif allow is not None and tc["name"] not in allow:
                result = (
                    f"[The '{tc['name']}' tool is not available here. You may use "
                    f"only: {', '.join(allow)}. Use one of those, or reply with "
                    "your final summary.]"
                )
                tool_result = None
            else:
                # Deterministic arg repair + validation (PRD Epic G): fix the safe
                # slips (raw JSON, trailing comma, "10"->10) and refuse a call with
                # missing/mistyped required args with a typed error instead of
                # executing it and looping on a confusing failure.
                _tool = tool_get(tc["name"])
                _ro = bool(_tool.read_only) if _tool else False
                _errs: list[str] = []
                if _tool is not None:
                    tc["input"], _errs, _ = repair_and_validate(_tool.schema, tc["input"] or {})
                _refusal = None
                if _errs:
                    _refusal = (
                        f"[{INVALID_ARGUMENTS}] {tc['name']} was not run — its "
                        f"arguments are invalid: {'; '.join(_errs)}. Fix them and "
                        f"call {tc['name']} again."
                    )
                # Effect-based approval (PRD Epic H): external mutations / credential
                # access / destructive tools need the operator's okay first. Bash
                # keeps its own finer command-level approval (bash_safety), so it's
                # exempt here.
                elif tc["name"] != "Bash" and not config.get("_approve_all") \
                        and tool_requires_approval(tc["name"], read_only=_ro, config=config):
                    approver = config.get("request_approval")
                    if approver is not None:
                        _eff = tool_effect_of(tc["name"], _ro)
                        decision = approver(
                            f"{tc['name']}  {str(tc['input'])[:120]}", f"{_eff.value} action")
                        if decision == "always":
                            config["_approve_all"] = True
                        elif decision != "allow":
                            _refusal = (
                                f"REFUSED: you declined to approve this "
                                f"{tc['name']} call ({_eff.value})."
                            )
                if _refusal is not None:
                    result = _refusal
                    tool_result = None
                else:
                    # Mark the action STARTED before running it (PRD P1.1/P2.2): a
                    # tool_started with no matching tool_completed in the trace means
                    # the process died mid-tool, so resume knows it's unresolved. The
                    # effect lets resume decide whether it's safe to retry (read-only)
                    # or needs approval (external mutation, P2.3).
                    _emit(state, "tool_started", name=tc["name"],
                          canonical=canonical_name(tc["name"]),
                          effect=tool_effect_of(tc["name"], _ro).value,
                          input=str(tc.get("input"))[:200])
                    tool_result = execute_structured(tc["name"], tc["input"], config)
                    result = tool_result.text
            # Emit the completion event IMMEDIATELY after execution (before
            # annotate/verify/progress post-processing) so the trace reads in
            # execution order — ▶ start → ✓ done → its progress/verification —
            # and result_chars reflects the RAW result, not one with advisory
            # notes prepended.
            _canon = canonical_name(tc["name"])
            if tool_result is not None:
                _emit(state, "tool", input=str(tc.get("input"))[:200],
                      canonical=_canon, **tool_result.to_event())
            else:
                _emit(state, "tool", name=tc["name"], canonical=_canon,
                      input=str(tc.get("input"))[:200],
                      result_chars=len(str(result)),
                      status="ok")
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
            _raw_result = result  # outcome identity = the un-annotated body
            result = loop_tracker.annotate(tc["name"], tc["input"], result)
            # Cycling detection (gov162 bench, large-scale-text-editing): the same
            # exact (call, result) pair recurring — NON-consecutively — is a loop
            # even when interleaved actions look productive (an identical macro
            # rewritten 22× between exit-0 test runs). annotate() just recorded
            # this pair, so the count includes the current call. Polling is exempt
            # (its result changes → different pair each time).
            _same_outcome = loop_tracker.outcome_count(tc["name"], tc["input"], _raw_result)

            # Verification evidence: if this Bash call was a test/check/exec, parse
            # its result so the completion gate knows whether it PASSED, not just ran.
            _repeated_outcome = False
            if tc["name"] == "Bash":
                _vcmd = (tc.get("input") or {}).get("command", "")
                if looks_like_verification(_vcmd):
                    last_verification = parse_evidence(_vcmd, result)
                    # Accumulate what the checks touched (command + result text),
                    # capped, for criteria-coverage at completion time.
                    if len(verification_text) < 200_000:
                        verification_text += f"\n{_vcmd}\n{result[:2000]}"
                    _emit(state, "verification", status=last_verification.status,
                          exit_code=last_verification.exit_code)
                    # Repeated-outcome detection (PRD Epic J3): the SAME failing
                    # verification outcome recurring — regardless of which command
                    # produced it (pytest vs python -m pytest vs a reworded check)
                    # — is a stall signal even though no exact call repeats. Keyed
                    # on the parsed failure summary, so a CHANGING failure (real
                    # progress) never counts. Found in the wild: a bench task ran
                    # 25 failing verifications in 38 turns with zero recovery
                    # because every command differed slightly.
                    if last_verification.status == "fail":
                        _vkey = last_verification.summary or f"exit:{last_verification.exit_code}"
                        verif_fail_counts[_vkey] = verif_fail_counts.get(_vkey, 0) + 1
                        _repeated_outcome = verif_fail_counts[_vkey] >= 2

            # Progress evaluation (PRD Epic L): score what this action actually
            # achieved and slide it through a window so a stalled run — repeated
            # actions that change nothing — is noticed even when no single call
            # repeats byte-for-byte. Advisory: it recommends a recovery stage,
            # it does not stop the loop.
            _failing_before = prev_failing
            _failing_after = None
            if last_verification is not None and tc["name"] == "Bash" \
                    and looks_like_verification((tc.get("input") or {}).get("command", "")):
                _failing_after = last_verification.tests_failed
                prev_failing = _failing_after
            assessment = progress_tracker.record(assess_action(
                # OUTCOME-aware repeat count: "repeated action without new
                # information" means the same call returned the SAME result again
                # — a poll whose output changes each time is novel information and
                # must not accrue stall (found suppressing legit polling).
                changed_state=bool(tool_result and tool_result.changed_state),
                repeat_count=_same_outcome,
                failing_tests_before=_failing_before if _failing_after is not None else None,
                failing_tests_after=_failing_after,
                repeated_outcome=_repeated_outcome,
            ))
            _emit(state, "progress", score=assessment.progress_score,
                  types=assessment.progress_types,
                  cumulative=progress_tracker.cumulative(),
                  streak=progress_tracker.no_progress_streak())

            # Recovery escalation (PRD Epic K): turn the progress window's verdict
            # into graduated action. Advisory stages prepend guidance to this
            # result; stage 3 suppresses THIS looping signature next time; stage 5
            # asks the loop to stop honestly. The offender is the current call —
            # only suppressed when the window says it's a no-progress loop.
            _rec_stage = progress_tracker.recommended_stage()
            # A call whose exact (call, result) pair has recurred past the
            # threshold is cycling regardless of the global streak (interleaved
            # "productive" actions kept resetting it) — floor the recommendation
            # at stage 3 so THIS signature gets suppressed. Cycles alternate with
            # other actions, so arm a window wide enough to outlast the period
            # (the default window expires exactly when a period-2 cycle returns).
            _cycling = _same_outcome >= config.get("recovery_same_outcome_threshold", 6)
            if _cycling:
                _rec_stage = max(_rec_stage or 0, 3)
            directive = recovery.escalate(
                _rec_stage,
                offender_signature=recovery_sig,
                iteration=run_iteration,
                suppress_window=(recovery.suppression_iterations + 3) if _cycling else None,
            )
            if directive.active:
                # Count real interventions (reflection and beyond, not the mild
                # advisory) against the recovery budget; hitting the ceiling forces
                # a controlled stop even if tool/iteration budget remains (PRD N1.4).
                if directive.stage >= 2:
                    budget.record_recovery()
                terminate = directive.terminate or budget.recovery_exhausted()
                _emit(state, "recovery", stage=directive.stage,
                      suppressed=directive.suppress, terminate=terminate,
                      attempts=budget.recovery_attempts)
                if directive.note:
                    result = f"{directive.note}\n{result}"
                if terminate:
                    recovery_terminate = True
            # Surface the governor's live state to the TUI status line.
            state.recovery_stage = recovery.stage
            state.progress_streak = progress_tracker.no_progress_streak()

            # Sync the rolling plan when the model updates its checklist, keeping
            # stable step ids + capping pending steps; record each revision.
            if tc["name"] == "todo" and isinstance(config.get("_todo"), list):
                if state.task.plan.update_from_items(config["_todo"]):
                    _emit(state, "plan", version=state.task.plan.version,
                          steps=[(s["id"], s["status"]) for s in state.task.plan.steps])

            yield ToolEnd(tc["name"], result)

            # Append tool result
            state.messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": tc["name"],
                "content": result,
            })

        # Recovery ceiling (PRD Epic K, stage 5): the progress window stayed
        # negative through every escalation stage. Stop the run honestly rather
        # than spin to MAX_TOOL_TURNS — the stage-5 note was already injected so
        # the model's final message reports the truth instead of claiming success.
        if recovery_terminate:
            yield TextChunk(
                "\n[Stopped: repeated attempts stopped making progress and "
                "escalating recovery did not help. Control is back to you.]\n"
            )
            break

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

        # Durable snapshot (PRD P2.1): persist the transcript + task state here, at
        # a consistent point, so a crash/kill can resume this task. Gated on a
        # configured path; atomic + defensive (never breaks the run).
        _resume_path = config.get("resume_path")
        if _resume_path:
            from drydock import resume as _resume
            _resume.save_snapshot(state, config, _resume_path)

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

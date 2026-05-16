"""Auto-solve hook — execute Z3 ourselves and inject a synthetic
solve() tool call + result before the LLM turn begins.

The escalation level above the smart-template engine. Even with a
pre-filled template in a system note, Gemma 4 wasn't calling `solve`
(0/153 sessions in the 2026-05-15 burndown). This module closes the
gap by computing the answer with Z3 ourselves and presenting it to
the model as authoritative tool output — same pattern as the
GraphRAG auto-prefetch.

Flow:
    user message arrives
        → constraint_extract.extract() pulls f(x) + predicate
        → build SolveArgs from the ExtractResult
        → run Solve tool synchronously (5-30s timeout)
        → if Z3 succeeded with a confident result:
            inject synthetic assistant message: tool_call=solve(...)
            inject synthetic tool message:      result=Z3's actual answer
            inject system note: "Use the count above as your answer."
        → real LLM turn proceeds with the authoritative result in context

Predicates handled:
    perfect_square, perfect_cube — count integer (x,y) with y²=f(x)
    equation_solve              — find integer solutions to f(x)=g(x)
    smallest_with / largest_with — optimize min/max
    divisible_by                 — count solutions in range with f(x)%k==0

Skipped (Z3 can't decide):
    prime — requires factoring; route via number_theory.is_prime in the
            normal advisory template instead.

Env gates:
    DRYDOCK_AUTO_SOLVE=0   — disable the synthetic-tool-call path
                              (smart-template advisory note still fires)
    DRYDOCK_AUTO_SOLVE_TIMEOUT_MS=<int>  — per-Z3-call timeout, default 10000

Safety:
    - Bounds are heuristic (x ∈ [-200, 200]). If Z3 returns sat for a
      bounded search, we report the count CONDITIONAL on the bound
      ("4 solutions in [-200, 200]"). The model can override if it
      knows the true range from the question.
    - If Z3 returns unsat / unknown / timeout, we emit nothing. The
      smart-template advisory falls through unchanged.
    - The synthetic tool call uses op="find_all" / "optimize" matching
      what the model would write — so messages.jsonl reads naturally.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import TYPE_CHECKING

logger = logging.getLogger("drydock.auto_solve")

if TYPE_CHECKING:
    from drydock.core.constraint_extract import ExtractResult


# Bound heuristics per predicate (default search range for `find_all`
# / `optimize`). These are intentionally narrow — Z3 enumerates within
# the range and we report `len(models)`. The model can manually
# re-call solve with a wider range if the problem demands it.
_DEFAULT_BOUNDS = {
    "perfect_square":  (-200, 200),
    "perfect_cube":    (-200, 200),
    "equation_solve":  (-200, 200),
    "divisible_by":    (1, 100),
    "smallest_with":   (1, 1000),
    "largest_with":    (1, 1000),
}


def _build_args(extr: "ExtractResult") -> dict | None:
    """Build the SolveArgs dict for this ExtractResult, or None if the
    predicate isn't one Z3 can decide. The dict is what the model
    *would* have written when calling the tool — used both for the
    actual Z3 call and as the synthetic tool_call arguments.
    """
    if extr.predicate == "prime":
        # Z3 can't check primality; advisory template routes via
        # number_theory.is_prime instead.
        return None
    if not extr.variables:
        return None

    main_var = extr.variables[0]
    lo, hi = _DEFAULT_BOUNDS.get(extr.predicate, (-100, 100))
    vars_csv = ", ".join(f"{v}:Int" for v in extr.variables)

    if extr.predicate == "perfect_square":
        return {
            "op": "find_all",
            "variables": f"{vars_csv}, y:Int",
            "constraints": [
                f"y * y == {extr.formula}",
                f"{main_var} >= {lo}", f"{main_var} <= {hi}",
                "y >= 0",
            ],
            "limit": 100,
            "timeout_ms": 30000,
        }
    if extr.predicate == "perfect_cube":
        return {
            "op": "find_all",
            "variables": f"{vars_csv}, y:Int",
            "constraints": [
                f"y * y * y == {extr.formula}",
                f"{main_var} >= {lo}", f"{main_var} <= {hi}",
            ],
            "limit": 100,
            "timeout_ms": 30000,
        }
    if extr.predicate == "divisible_by":
        return {
            "op": "find_all",
            "variables": vars_csv,
            "constraints": [
                f"({extr.formula}) % {extr.divisor} == 0",
                f"{main_var} >= {lo}", f"{main_var} <= {hi}",
            ],
            "limit": 200,
            "timeout_ms": 15000,
        }
    if extr.predicate == "equation_solve":
        if not extr.second_formula:
            return None
        return {
            "op": "find_all",
            "variables": vars_csv,
            "constraints": [
                f"({extr.formula}) == ({extr.second_formula})",
                f"{main_var} >= {lo}", f"{main_var} <= {hi}",
            ],
            "limit": 50,
            "timeout_ms": 15000,
        }
    if extr.predicate in ("smallest_with", "largest_with"):
        return {
            "op": "optimize",
            "variables": vars_csv,
            "constraints": [
                extr.formula,
                f"{main_var} >= {lo}", f"{main_var} <= {hi}",
            ],
            "objective": main_var,
            "direction": "min" if extr.predicate == "smallest_with" else "max",
            "timeout_ms": 15000,
        }
    return None


def _run_solve_sync(args_dict: dict) -> dict | None:
    """Invoke the Solve tool with the given args and return the
    SolveResult as a dict. Returns None on failure / timeout / unsat.
    """
    try:
        from drydock.core.tools.base import BaseToolState
        from drydock.core.tools.builtins.solve_tool import (
            Solve, SolveArgs, SolveConfig, SolveResult,
        )
    except Exception as e:
        logger.warning("[AUTO-SOLVE] could not import Solve tool: %s", e)
        return None

    try:
        args = SolveArgs(**args_dict)
    except Exception as e:
        logger.warning("[AUTO-SOLVE] bad SolveArgs: %s; args=%s", e, args_dict)
        return None

    tool = Solve(config=SolveConfig(), state=BaseToolState())

    async def _run() -> SolveResult | None:
        result = None
        async for ev in tool.run(args):
            if isinstance(ev, SolveResult):
                result = ev
        return result

    try:
        # Hard wall clock — Z3's internal timeout is per-check; this
        # bounds the whole orchestration including streaming setup.
        wall_ms = int(args_dict.get("timeout_ms", 15000)) + 5000
        result = asyncio.run(asyncio.wait_for(_run(), timeout=wall_ms / 1000))
    except asyncio.TimeoutError:
        logger.warning("[AUTO-SOLVE] hard timeout exceeded")
        return None
    except RuntimeError as e:
        # Likely "asyncio.run() cannot be called from a running event loop"
        # if we're somehow inside one — fall through.
        logger.warning("[AUTO-SOLVE] asyncio error: %s", e)
        return None
    except Exception as e:  # noqa: BLE001 — solver failures must not crash drydock
        logger.warning("[AUTO-SOLVE] solver failure: %s", e, exc_info=True)
        return None

    if result is None or not result.ok:
        return None
    return result.model_dump()


def _format_result(args_dict: dict, result_dict: dict,
                   extr: "ExtractResult") -> str:
    """Render the Solve result the way the real Solve tool prints it,
    matching what the model sees from a normal tool call. Adds a
    one-line summary at the bottom so the model can extract the
    answer in one read.
    """
    status = result_dict.get("status", "?")
    model = result_dict.get("model", "")
    models = result_dict.get("models", []) or []
    obj = result_dict.get("objective_value", "")

    parts = [f"status: {status}"]
    if extr.predicate in ("perfect_square", "perfect_cube",
                          "equation_solve", "divisible_by"):
        # Counting questions — len(models) is the answer
        parts.append(f"solutions found: {len(models)}")
        if models:
            parts.append("solutions:")
            for m in models[:50]:
                parts.append(f"  {m}")
        # Single-line answer hint at the bottom
        if status == "sat":
            parts.append("")
            parts.append(f"ANSWER: {len(models)}  "
                         f"(Z3 enumerated all integer solutions to "
                         f"`{args_dict['constraints'][0]}` in the bounded range "
                         f"{args_dict['constraints'][1]} ∧ "
                         f"{args_dict['constraints'][2]})")
    elif extr.predicate in ("smallest_with", "largest_with"):
        if status == "optimal" and obj:
            parts.append(f"optimal value: {obj}")
            parts.append(f"assignment: {model}")
            parts.append("")
            parts.append(f"ANSWER: {obj}")
        else:
            parts.append(f"model: {model}")
    else:
        parts.append(f"model: {model}")

    return "\n".join(parts)


def maybe_inject_auto_solve(messages_obj, user_msg: str) -> bool:
    """Try to compute and inject a synthetic solve() call+result.

    Returns True if the pair was injected, False otherwise. The
    `messages_obj` is the agent_loop's `self.messages` — must
    expose `.append(LLMMessage)`.

    Env-gated by DRYDOCK_AUTO_SOLVE (default ON in production).
    Safe to call repeatedly per turn; the caller is responsible for
    not calling it twice for the same user message.
    """
    if os.environ.get("DRYDOCK_AUTO_SOLVE", "1").strip().lower() in (
            "0", "false", "no"):
        return False
    if not user_msg or len(user_msg) < 10:
        return False

    try:
        from drydock.core.constraint_extract import extract
    except Exception as e:
        logger.warning("[AUTO-SOLVE] could not import constraint_extract: %s", e)
        return False

    try:
        extr = extract(user_msg)
    except Exception as e:  # noqa: BLE001
        logger.warning("[AUTO-SOLVE] extract failed: %s", e)
        return False
    if extr is None or extr.confidence < 0.5:
        return False

    args_dict = _build_args(extr)
    if args_dict is None:
        logger.warning("[AUTO-SOLVE] predicate %s not Z3-decidable", extr.predicate)
        return False

    logger.warning("[AUTO-SOLVE] running Z3 for predicate=%s formula=%r",
                   extr.predicate, extr.formula[:80])
    result_dict = _run_solve_sync(args_dict)
    if result_dict is None:
        return False
    status = result_dict.get("status", "?")
    if status not in ("sat", "optimal", "valid"):
        # unsat / unknown / infeasible / countered: don't inject — the
        # advisory template still works as a fallback and the model
        # might do better by reasoning differently.
        logger.warning("[AUTO-SOLVE] Z3 status=%s; not injecting", status)
        return False

    # Build synthetic assistant + tool messages
    try:
        from drydock.core.types import LLMMessage, Role, ToolCall, FunctionCall
    except Exception as e:
        logger.warning("[AUTO-SOLVE] could not import types: %s", e)
        return False

    call_id = f"auto-solve-{uuid.uuid4().hex[:16]}"
    args_json = json.dumps(args_dict)
    formatted = _format_result(args_dict, result_dict, extr)

    synth_assistant = LLMMessage(
        role=Role.assistant,
        content="",
        tool_calls=[
            ToolCall(
                id=call_id,
                function=FunctionCall(name="solve", arguments=args_json),
                type="function",
            ),
        ],
    )
    synth_tool = LLMMessage(
        role=Role.tool,
        content=formatted,
        name="solve",
        tool_call_id=call_id,
    )
    messages_obj.append(synth_assistant)
    messages_obj.append(synth_tool)
    logger.warning("[AUTO-SOLVE] injected synthetic solve call+result "
                   "(predicate=%s, status=%s)", extr.predicate, status)
    return True

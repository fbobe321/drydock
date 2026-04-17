"""Phase 3a: per-(model, task) hyperparameter store and applier.

Admiral can tune certain knobs within bounded ranges based on the
policy loop's verdicts. The knob values are stored in
`~/.drydock/admiral_tuning.json`; the apply hook mutates AgentLoop
fields BEFORE the main loop starts.

Hard invariants:
1. Knob values are always clipped to the bounded range below.
2. Admiral never mutates source — only this JSON file.
3. Malformed JSON is ignored (logged) — tuning silently falls back
   to defaults rather than crashing the harness.
4. Unknown knob keys in the JSON are dropped with a warning.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from drydock.admiral import persistence

if TYPE_CHECKING:
    from drydock.core.agent_loop import AgentLoop

logger = logging.getLogger(__name__)

# Knob -> (min, max) bounded range. Anything outside is clipped.
KNOB_BOUNDS: dict[str, tuple[float, float]] = {
    "per_prompt_budget_sec": (300, 3600),           # 5 min .. 60 min
    "hard_stop_tool_calls": (30, 250),
    "wrap_up_warn_at": (10, 100),
    "stop_now_warn_at": (20, 150),
    "temperature": (0.0, 0.9),
    "loop_detector_window": (2, 6),
    "struggle_threshold": (10, 40),
}

# Supported task_type strings; unknowns silently pass through.
_VALID_TASKS = frozenset({"build", "bugfix", "explore", "refactor", "unknown"})


def _tuple_key(model: str, task: str) -> str:
    if task not in _VALID_TASKS:
        task = "unknown"
    return f"{model}+{task}"


def load_all() -> dict[str, dict[str, Any]]:
    raw = persistence.load_json(persistence.TUNING_PATH, default={})
    if not isinstance(raw, dict):
        return {}
    return raw


def save_all(data: dict[str, dict[str, Any]]) -> None:
    persistence.save_json_atomic(persistence.TUNING_PATH, data)


def get_for(model: str, task: str) -> dict[str, Any]:
    """Return the knob overrides for this tuple (possibly empty)."""
    all_tuning = load_all()
    entry = all_tuning.get(_tuple_key(model, task)) or {}
    # Clip everything to bounds before returning.
    clipped: dict[str, Any] = {}
    for k, v in entry.items():
        if k.startswith("_"):
            clipped[k] = v  # meta keys like _rationale
            continue
        if k not in KNOB_BOUNDS:
            logger.warning("Admiral tuning: unknown knob %r — ignoring", k)
            continue
        lo, hi = KNOB_BOUNDS[k]
        try:
            val = float(v)
        except (TypeError, ValueError):
            logger.warning("Admiral tuning: %s=%r not numeric — ignoring", k, v)
            continue
        clipped[k] = max(lo, min(hi, val))
    return clipped


def set_knob(model: str, task: str, knob: str, value: float, rationale: str = "") -> None:
    """Write one knob into the tuning JSON, bounded."""
    if knob not in KNOB_BOUNDS:
        raise ValueError(f"unknown knob: {knob}")
    lo, hi = KNOB_BOUNDS[knob]
    bounded = max(lo, min(hi, float(value)))
    all_tuning = load_all()
    key = _tuple_key(model, task)
    entry = all_tuning.setdefault(key, {})
    entry[knob] = bounded
    if rationale:
        entry["_rationale"] = rationale
    save_all(all_tuning)


def revert_knob(model: str, task: str, knob: str) -> None:
    all_tuning = load_all()
    entry = all_tuning.get(_tuple_key(model, task)) or {}
    if knob in entry:
        del entry[knob]
    save_all(all_tuning)


def apply_to_agent_loop(agent_loop: AgentLoop) -> dict[str, Any]:
    """Apply `(model, task=unknown)` knobs to the AgentLoop at init.

    Task type is re-evaluated after the first user prompt; if it
    changes, call `apply_to_agent_loop` again to re-tune.

    Returns the knobs that were actually applied (for logging).
    """
    if os.getenv("DRYDOCK_ADMIRAL_TUNING", "1") == "0":
        return {}
    try:
        model = agent_loop.config.get_active_model().name
    except Exception:
        return {}
    task = getattr(agent_loop, "_admiral_task_type", "unknown")
    knobs = get_for(model, task)
    applied: dict[str, Any] = {}
    for k, v in knobs.items():
        if k.startswith("_"):
            continue
        # Write to a well-known attribute on the agent loop — the
        # agent loop reads these at the top of its while-loop each
        # turn, so a mid-run re-apply takes effect next turn.
        setattr(agent_loop, f"_admiral_{k}", v)
        applied[k] = v
    return applied

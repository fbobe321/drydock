"""Session-end metrics for Admiral.

Collects a small set of numbers about how a session went, keyed by
`(model, task_type)`, and appends one JSONL line at the end. The
Phase 3a policy loop reads these to decide whether to adjust a
hyperparameter knob for that tuple.

The collection hook fires from AgentLoop on shutdown, best-effort
only — a failure to write a metric must never propagate.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from drydock.admiral import persistence, task_classifier
from drydock.core.types import LLMMessage, Role

if TYPE_CHECKING:
    from drydock.core.agent_loop import AgentLoop

logger = logging.getLogger(__name__)

_WRITE_TOOLS = frozenset({"write_file", "search_replace", "edit_file"})


@dataclass
class SessionMetrics:
    ts: str
    model: str
    task_type: str
    session_id: str
    outcome: str = "unknown"            # success | failure | unknown
    total_tool_calls: int = 0
    tool_calls_per_write: float = 0.0
    time_to_first_write_s: float | None = None
    loop_fires: int = 0
    struggle_fires: int = 0
    opus_escalations: int = 0
    user_interrupts: int = 0
    per_prompt_budget_hits: int = 0
    elapsed_s: float | None = None
    notes: list[str] = field(default_factory=list)


def _count_tool_calls(messages: Sequence[LLMMessage]) -> tuple[int, int]:
    total = writes = 0
    for m in messages:
        if m.role != Role.assistant or not m.tool_calls:
            continue
        for tc in m.tool_calls:
            total += 1
            if tc.function.name in _WRITE_TOOLS:
                writes += 1
    return total, writes


def collect(agent_loop: AgentLoop, session_id: str, outcome: str = "unknown") -> SessionMetrics:
    """Build a SessionMetrics record from the live AgentLoop state.

    Safe to call during shutdown — wraps message access in a try/except
    so partial state never breaks the collection.
    """
    try:
        msgs = list(agent_loop.messages)
    except Exception:
        msgs = []
    try:
        model_name = agent_loop.config.get_active_model().name
    except Exception:
        model_name = "unknown"
    total, writes = _count_tool_calls(msgs)
    tcpw = (total / writes) if writes else float(total)
    return SessionMetrics(
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        model=model_name,
        task_type=task_classifier.classify(msgs),
        session_id=session_id,
        outcome=outcome,
        total_tool_calls=total,
        tool_calls_per_write=round(tcpw, 2),
    )


def record(metrics: SessionMetrics) -> None:
    try:
        persistence.append_jsonl(persistence.METRICS_PATH, asdict(metrics))
    except Exception as e:
        logger.warning("Admiral metrics write failed: %s", e)

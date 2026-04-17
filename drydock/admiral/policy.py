"""Phase 3a adaptation policy.

Simple bounded rule engine — no ML. Runs over the session metrics
log and decides whether to adjust ONE knob for a `(model, task_type)`
tuple. Keeps changes slow on purpose: Admiral nudges one step per
re-evaluation window; if recovery doesn't happen, reverts.

Invariants:
* Never adjusts more than ONE knob per tuple per pass.
* Never adjusts before N sessions are observed for the tuple.
* Always records rationale in the tuning file.
* Reads are lock-free: if two loops race, the JSON write is atomic
  via persistence.save_json_atomic.

Disabled by default (reads `DRYDOCK_ADMIRAL_POLICY` env var; set to
`1` to enable). Users opt in when ready.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from statistics import mean
from typing import Any

from drydock.admiral import persistence, tuning

logger = logging.getLogger(__name__)

MIN_SESSIONS_TO_TUNE = 5
SUCCESS_REGRESSION_THRESHOLD = 0.2  # 20% drop in success rate
RECOVERY_WINDOW = 3                 # sessions to evaluate after a tweak


def _load_recent_metrics(limit: int = 100) -> list[dict]:
    path = persistence.METRICS_PATH
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with path.open() as f:
            for line in f.readlines()[-limit:]:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out


def _group_by_tuple(metrics: list[dict]) -> dict[tuple[str, str], list[dict]]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for m in metrics:
        key = (m.get("model", "unknown"), m.get("task_type", "unknown"))
        groups[key].append(m)
    return groups


def _success_rate(records: list[dict]) -> float:
    if not records:
        return 0.0
    return sum(1 for r in records if r.get("outcome") == "success") / len(records)


def _pick_knob_to_tune(records: list[dict]) -> tuple[str, float, str] | None:
    """Very simple heuristic: correlate the top failure pattern with
    one knob. Returns (knob, new_value, rationale) or None.
    """
    if not records:
        return None
    # Many budget hits → raise per_prompt_budget_sec.
    if mean(r.get("per_prompt_budget_hits", 0) for r in records) >= 0.4:
        return (
            "per_prompt_budget_sec",
            1800.0 * 1.25,  # +25% from 30min baseline, clipped to bound
            "per_prompt_budget_hits ≥0.4 per session — raise budget.",
        )
    # Many struggle fires → raise struggle_threshold (reads needed).
    if mean(r.get("struggle_fires", 0) for r in records) >= 1.0:
        return (
            "struggle_threshold",
            30.0,
            "struggle_fires ≥1/session — give explore turns more budget.",
        )
    # Many loop fires → tighten loop_detector_window (catch loops earlier).
    if mean(r.get("loop_fires", 0) for r in records) >= 1.0:
        return (
            "loop_detector_window",
            2.0,
            "loop_fires ≥1/session — trip loop detector earlier.",
        )
    return None


def evaluate() -> list[dict[str, Any]]:
    """Run one policy pass. Returns list of changes applied (possibly empty)."""
    if os.getenv("DRYDOCK_ADMIRAL_POLICY", "0") != "1":
        return []

    metrics = _load_recent_metrics()
    groups = _group_by_tuple(metrics)
    changes: list[dict[str, Any]] = []
    for (model, task), records in groups.items():
        if len(records) < MIN_SESSIONS_TO_TUNE:
            continue
        recent = records[-MIN_SESSIONS_TO_TUNE:]
        rate = _success_rate(recent)
        # Baseline = across ALL records for this tuple.
        baseline = _success_rate(records)
        if baseline == 0 or rate >= baseline - SUCCESS_REGRESSION_THRESHOLD:
            # Performing fine — don't tune.
            continue
        pick = _pick_knob_to_tune(recent)
        if not pick:
            continue
        knob, value, rationale = pick
        # Idempotency: if the same knob is already set, skip.
        current = tuning.get_for(model, task)
        if knob in current:
            # Already tuning this knob; give it RECOVERY_WINDOW sessions
            # to show improvement before we touch it again.
            if len(recent) < RECOVERY_WINDOW + MIN_SESSIONS_TO_TUNE:
                continue
        tuning.set_knob(model, task, knob, value, rationale=rationale)
        changes.append({
            "model": model,
            "task": task,
            "knob": knob,
            "value": value,
            "rationale": rationale,
        })
    return changes

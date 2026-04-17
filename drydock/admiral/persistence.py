"""Cross-session state for Admiral.

Three files under ~/.drydock/:
* `admiral_state.json` — finding counters for Phase 3b promotion
  criteria (same code needs to fire in ≥3 sessions before Admiral
  proposes a code change).
* `admiral_tuning.json` — Phase 3a per-(model, task) hyperparameters.
* `admiral_metrics.jsonl` — one line per finished session; the policy
  loop reads this to decide whether to tune a knob.

All writes are atomic (temp-file + rename). Malformed files are
logged and ignored rather than crashing the harness — Admiral must
never take drydock down.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_PATH = Path.home() / ".drydock" / "admiral_state.json"
TUNING_PATH = Path.home() / ".drydock" / "admiral_tuning.json"
METRICS_PATH = Path.home() / ".drydock" / "logs" / "admiral_metrics.jsonl"
PROPOSALS_DIR = Path.home() / ".drydock" / "admiral_proposals"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception as e:
        logger.warning("Admiral state at %s is malformed, ignoring: %s", path, e)
        return default


def save_json_atomic(path: Path, data: Any) -> None:
    _ensure_parent(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str))
    os.replace(tmp, path)


def append_jsonl(path: Path, obj: Any) -> None:
    _ensure_parent(path)
    with path.open("a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def load_state() -> dict[str, Any]:
    return load_json(STATE_PATH, default={})


def save_state(state: dict[str, Any]) -> None:
    save_json_atomic(STATE_PATH, state)


def record_finding(code: str, session_id: str) -> dict[str, Any]:
    """Count occurrences of a finding code across sessions.

    Returns the updated per-code stats:
      {"sessions": [ids], "total_fires": int, "last_seen": iso8601}
    """
    state = load_state()
    findings = state.setdefault("findings", {})
    entry = findings.setdefault(code, {"sessions": [], "total_fires": 0, "last_seen": None})
    if session_id and session_id not in entry["sessions"]:
        entry["sessions"].append(session_id)
    entry["total_fires"] = int(entry["total_fires"]) + 1
    entry["last_seen"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(state)
    return entry


def record_intervention_outcome(code: str, unstuck: bool) -> None:
    """Track whether a prompt-only intervention actually unstuck the agent.

    Same code re-firing within 10 turns after an intervention = failed.
    """
    state = load_state()
    findings = state.setdefault("findings", {})
    entry = findings.setdefault(code, {"sessions": [], "total_fires": 0, "last_seen": None})
    entry.setdefault("prompt_unstuck", 0)
    entry.setdefault("prompt_failed", 0)
    if unstuck:
        entry["prompt_unstuck"] = int(entry["prompt_unstuck"]) + 1
    else:
        entry["prompt_failed"] = int(entry["prompt_failed"]) + 1
    save_state(state)


def finding_qualifies_for_code_change(code: str, min_sessions: int = 3) -> bool:
    """Phase 3b promotion criteria: finding must have fired in ≥N
    sessions AND at least one prompt-only intervention must have failed.
    """
    state = load_state()
    entry = state.get("findings", {}).get(code)
    if not entry:
        return False
    if len(entry.get("sessions", [])) < min_sessions:
        return False
    return int(entry.get("prompt_failed", 0)) >= 1


def record_proposal_fingerprint(code: str, fingerprint: str, status: str) -> None:
    """After /admiral-apply or /admiral-reject — never re-propose the same patch."""
    state = load_state()
    props = state.setdefault("proposals", {})
    props.setdefault(code, []).append({
        "fingerprint": fingerprint,
        "status": status,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    save_state(state)


def is_fingerprint_rejected(code: str, fingerprint: str) -> bool:
    state = load_state()
    return any(
        p.get("fingerprint") == fingerprint and p.get("status") == "rejected"
        for p in state.get("proposals", {}).get(code, [])
    )

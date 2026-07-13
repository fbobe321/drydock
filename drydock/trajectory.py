"""Verified-trajectory export — the training-data side of the RSI loop.

When `trajectory_file` is set (a benchmark harness sets it per task), drydock writes
the FULL task trajectory — system prompt + the complete message transcript (every
assistant turn with its tool calls, every tool result) — to that file at task end.
The harness then keeps ONLY the trajectories the deterministic verifier confirmed
correct, so the resulting corpus is solutions that provably work (no self-grading).

That corpus is the seed for self-distillation and the same format a stronger teacher
model feeds into. Export is opt-in and fully defensive — it must never affect a run.
All logic original to Drydock.
"""
from __future__ import annotations

import json
from pathlib import Path

_MAX_FIELD = 200_000  # cap any single message body so one huge tool dump can't bloat the record


def _clip(v):
    if isinstance(v, str) and len(v) > _MAX_FIELD:
        return v[:_MAX_FIELD] + f"\n[… {len(v) - _MAX_FIELD} chars truncated]"
    return v


def _sanitize(messages: list) -> list:
    """A JSON-safe copy of the transcript with oversized bodies clipped."""
    out = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        mm = {k: _clip(v) for k, v in m.items()}
        out.append(mm)
    return out


def build_record(system_prompt: str, state, config: dict, meta: dict | None = None) -> dict:
    """Assemble the trajectory record (does not write). Verified/reward are left None
    for the harness to stamp after the verifier runs."""
    task = getattr(state, "task", None)
    tools = sorted({m.get("name", "") for m in state.messages
                    if isinstance(m, dict) and m.get("role") == "tool" and m.get("name")})
    return {
        "task": (meta or {}).get("task", ""),
        "model": config.get("model", ""),
        "objective": getattr(task, "objective", "") if task else "",
        "acceptance_criteria": list(getattr(task, "acceptance_criteria", []) or []) if task else [],
        "phase": getattr(task, "phase", "") if task else "",
        "system": system_prompt,
        "messages": _sanitize(state.messages),
        "n_messages": len(state.messages),
        "tools": tools,
        "in_tokens": getattr(state, "total_input_tokens", 0),
        "out_tokens": getattr(state, "total_output_tokens", 0),
        "verified": None,   # stamped True by the collector iff the verifier passed
        "reward": None,
    }


def record(system_prompt: str, state, config: dict, meta: dict | None = None):
    """Write the current full trajectory to config['trajectory_file'] (overwrite, so
    it always holds the latest complete transcript). No-op if unset. Never raises."""
    path = config.get("trajectory_file")
    if not path:
        return None
    try:
        p = Path(path)
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(build_record(system_prompt, state, config, meta), default=str),
                     encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 — export must never break a run
        return None

"""JSONL queue at ~/.drydock/dispatch/curiosity.jsonl.

Mirrors the dispatcher pattern in `drydock.core.classifier.dispatcher`
but for learning signals instead of failure signals. The classifier
already owns `~/.drydock/dispatch/<bucket>.jsonl` for HARNESS / RETRIEVAL
/ STEERING / MODEL_PRIOR / AMBIGUOUS_INPUT / OTHER; this adds a
`curiosity.jsonl` sibling that `autonomous_review.sh` and the Phase-3
idle-cycle worker both consume.

Append-only. Dedup is by fingerprint within a 7-day window so a recurring
unknown term doesn't blow the queue up.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from drydock.curiosity.item import CuriosityItem

_DEFAULT_ROOT = Path.home() / ".drydock" / "dispatch"
_DEDUP_WINDOW_DAYS = 7


def queue_path(root: Path | None = None) -> Path:
    """Resolve the curiosity queue file. Env override:
    DRYDOCK_CURIOSITY_QUEUE → full path to the .jsonl file."""
    override = os.environ.get("DRYDOCK_CURIOSITY_QUEUE")
    if override:
        return Path(override)
    base = root or _DEFAULT_ROOT
    return base / "curiosity.jsonl"


def _recent_fingerprints(path: Path, window_days: int = _DEDUP_WINDOW_DAYS) -> set[str]:
    """Read fingerprints written in the last `window_days` for dedup."""
    if not path.is_file():
        return set()
    cutoff = time.time() - window_days * 86400
    seen: set[str] = set()
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                fp = d.get("extra", {}).get("fingerprint") or ""
                ts_iso = d.get("ts") or ""
                if not fp:
                    continue
                # Best-effort parse of ISO ts. If it's unparseable, treat as
                # recent (conservative — better dedup than spam).
                try:
                    t = time.mktime(time.strptime(ts_iso, "%Y-%m-%dT%H:%M:%SZ"))
                    if t < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass
                seen.add(fp)
    except OSError:
        pass
    return seen


def enqueue(item: CuriosityItem, root: Path | None = None) -> bool:
    """Append `item` to the curiosity queue. Returns True if written,
    False if deduped against a recent identical fingerprint.

    The caller doesn't have to set `id` or `ts` — this fills them in.
    """
    path = queue_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fp = item.fingerprint()
    if fp in _recent_fingerprints(path):
        return False
    item.id = fp
    item.ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = item.to_jsonable()
    payload.setdefault("extra", {})["fingerprint"] = fp
    with path.open("a") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return True


def read_recent(limit: int = 50, root: Path | None = None) -> list[dict]:
    """Return the last `limit` entries (newest last) as dicts.

    Useful for tests + the autonomous_review consumer that picks the
    next item to act on.
    """
    path = queue_path(root)
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out[-limit:]

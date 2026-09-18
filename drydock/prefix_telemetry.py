"""Prefix-difference telemetry — how much of each successive Drydock prompt is
actually identical to the last one?

Cache-aware MCR spec (docs/cache_aware_mcr_spec.md) §32 item 2: *before* changing any
behaviour, measure the reuse that real runs already get. Appendix A.1 established that
re-prefill cost scales with how early a prompt changes; this records, per inference
call, where successive prompts actually diverge — so claims about cache-aware layout
can be checked against a real baseline instead of assumed.

Measures the payload that genuinely reaches the model (`oai_messages`), not Drydock's
internal state. Deliberately OFF by default: the shipped package must not acquire a
write side-effect. Enable with config `prefix_telemetry: true` or
`DRYDOCK_PREFIX_TELEMETRY=1`.

Stdlib-only, swallow-all-errors — measurement must never break a run.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


def enabled(config: dict | None) -> bool:
    cfg = config or {}
    return bool(cfg.get("prefix_telemetry")
                or os.environ.get("DRYDOCK_PREFIX_TELEMETRY"))


def canonical(oai_messages: list) -> str:
    """Deterministic serialization of the outgoing messages. Order is preserved (it is
    the prompt order), so this stands in for the token stream: identical text implies
    identical tokens for a fixed tokenizer."""
    try:
        return json.dumps(oai_messages, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(oai_messages)


def common_prefix_len(a: str, b: str) -> int:
    """Length in characters of the longest common prefix."""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def message_divergence(prev: list, cur: list) -> int:
    """Index of the first differing message, or len(common span) if one is a prefix of
    the other. Message-level granularity localises WHICH part of the prompt moved —
    a rewritten system prompt and an appended tool result look identical in a raw
    character diff but have very different cache consequences."""
    n = min(len(prev or []), len(cur or []))
    for i in range(n):
        if canonical([prev[i]]) != canonical([cur[i]]):
            return i
    return n


def _est_tokens(chars: int) -> int:
    """Same chars/3.0 basis the compactor and MCR use, so numbers are comparable."""
    return int(chars / 3.0)


class PrefixTelemetry:
    """Per-session recorder. One row per inference call."""

    def __init__(self, root: str = ".", session: str = ""):
        self.session = session or f"sess-{int(time.time())}"
        self.dir = Path(root) / ".drydock" / "research" / "prefix_diff"
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.path = self.dir / f"{self.session}.jsonl"
        self._prev_text: str = ""
        self._prev_msgs: list = []
        self._seq = 0

    def record(self, oai_messages: list, model: str = "") -> dict:
        """Compare this prompt with the previous one and append a row. Never raises."""
        row: dict = {}
        try:
            text = canonical(oai_messages)
            lcp = common_prefix_len(self._prev_text, text) if self._prev_text else 0
            total = len(text)
            self._seq += 1
            row = {
                "seq": self._seq,
                "ts": time.time(),
                "model": model,
                "messages": len(oai_messages or []),
                "prompt_chars": total,
                "prompt_tokens_est": _est_tokens(total),
                "reusable_prefix_chars": lcp,
                "reusable_prefix_tokens_est": _est_tokens(lcp),
                "uncached_tokens_est": _est_tokens(max(0, total - lcp)),
                "reuse_pct": round(100.0 * lcp / total, 1) if total else 0.0,
                "diverged_at_message": message_divergence(self._prev_msgs, oai_messages or []),
                "prev_messages": len(self._prev_msgs),
            }
            try:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            except (OSError, TypeError, ValueError):
                pass
            self._prev_text = text
            self._prev_msgs = list(oai_messages or [])
        except Exception:  # noqa: BLE001 — telemetry must never break a run
            pass
        return row


# Recorders are held HERE, not on the config dict. The config is shallow-copied per
# call, so a recorder stashed on it is rebuilt every time — which silently produces one
# row per file, seq=1, reuse 0.0, i.e. telemetry that measures nothing. (Observed on the
# first live run.) Keying on (thread, cwd) follows the actual agent run instead: the TUI
# runs its agent in one worker thread, and each swarm worker has its own thread, so
# concurrent runs stay separated without depending on config plumbing.
_REGISTRY: "dict[tuple, PrefixTelemetry]" = {}
_LOCK = threading.Lock()


def reset_registry() -> None:
    """Drop all recorders (tests, or starting a fresh measurement)."""
    with _LOCK:
        _REGISTRY.clear()


def record_for(config: dict | None, oai_messages: list, model: str = "") -> dict:
    """Hook used by the provider. No-op unless explicitly enabled."""
    if not enabled(config):
        return {}
    try:
        cwd = str((config or {}).get("cwd") or ".")
        key = (threading.get_ident(), cwd)
        with _LOCK:
            rec = _REGISTRY.get(key)
            if rec is None:
                rec = PrefixTelemetry(root=cwd)
                _REGISTRY[key] = rec
        return rec.record(oai_messages, model=model)
    except Exception:  # noqa: BLE001
        return {}

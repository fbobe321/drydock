"""Dispatcher — consume FailureSignals and route per bucket.

The classifier emits structured `FailureSignal` records. The
dispatcher decides what to DO with each one. Per Sovereign v2 design:

  harness         → queue a fix candidate the harness dispatcher
                    (autonomous_review or successor) consumes
  retrieval       → queue a corpus-gap entry for GraphRAG curation
  steering        → queue a Deep Noir vector-candidate entry
  model_prior     → queue a LoRA training-data candidate
  ambiguous_input → surface to the operator (not a drydock fix)
  other           → surface to operator + log for taxonomy review

For v0 the default handlers all write to per-bucket JSONL queues
under `~/.drydock/dispatch/<bucket>.jsonl`. Real downstream automation
(autonomous_review's auto-PR mode, GraphRAG corpus curator, etc.)
reads those queues. A deployment can override any handler by passing
a custom callable in the constructor.

Design notes:
- Dedup by `pattern_id + evidence` per dispatch run, so re-running
  the classifier on the same log doesn't blow the queue up.
- The dispatcher is **pure orchestration** — handlers do the work.
  Tests can stub handlers cleanly.
- Failures in one handler never break others; we log and continue.

Public surface:
    Dispatcher, DispatchResult, default_handler_for
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from drydock.core.classifier.signal import Bucket, FailureSignal

logger = logging.getLogger(__name__)


# Type alias — a handler takes one signal and returns nothing useful.
# Called for each signal in its target bucket.
DispatchHandler = Callable[[FailureSignal], None]


def _default_queue_root() -> Path:
    return Path.home() / ".drydock" / "dispatch"


def _queue_path_for(bucket: Bucket, root: Path | None = None) -> Path:
    return (root or _default_queue_root()) / f"{bucket}.jsonl"


def make_jsonl_handler(bucket: Bucket, root: Path | None = None) -> DispatchHandler:
    """Build a handler that appends each signal as one JSON line under
    ~/.drydock/dispatch/<bucket>.jsonl (or `root`/<bucket>.jsonl).

    Atomicity: each write is one line per signal, with a timestamp.
    The append is open-write-flush-close so concurrent runs don't
    interleave half-lines."""
    path = _queue_path_for(bucket, root)
    path.parent.mkdir(parents=True, exist_ok=True)

    def handler(signal: FailureSignal) -> None:
        record = signal.to_jsonable()
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")
            f.flush()

    return handler


def default_handler_for(bucket: Bucket, root: Path | None = None) -> DispatchHandler:
    """Per-bucket default — JSONL queue for everything; harness
    deployments may want a more direct integration later."""
    return make_jsonl_handler(bucket, root)


@dataclass
class DispatchResult:
    """What one dispatcher run produced. Useful for trip-log style
    summaries and as the return value of `Dispatcher.dispatch_all`."""
    dispatched: int = 0
    deduped: int = 0
    by_bucket: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bucket_str = ", ".join(
            f"{b}={c}" for b, c in sorted(self.by_bucket.items())
        ) or "no signals"
        out = f"dispatched {self.dispatched} signals ({bucket_str})"
        if self.deduped:
            out += f"; deduped {self.deduped}"
        if self.errors:
            out += f"; errors={len(self.errors)}"
        return out


class Dispatcher:
    """Routes FailureSignals to per-bucket handlers."""

    def __init__(
        self,
        handlers: dict[Bucket, DispatchHandler] | None = None,
        queue_root: Path | None = None,
    ):
        # If the caller passes partial overrides, fill the rest with defaults.
        defaults = {
            bucket: default_handler_for(bucket, queue_root)
            for bucket in Bucket
        }
        if handlers:
            defaults.update(handlers)
        self.handlers: dict[Bucket, DispatchHandler] = defaults

    def dispatch_all(
        self, signals: Iterable[FailureSignal]
    ) -> DispatchResult:
        result = DispatchResult()
        seen: set[tuple[str, str]] = set()
        bucket_counts: Counter[str] = Counter()

        for signal in signals:
            key = (signal.pattern_id, signal.evidence)
            if key in seen:
                result.deduped += 1
                continue
            seen.add(key)

            handler = self.handlers.get(signal.bucket)
            if handler is None:
                result.errors.append(
                    f"no handler for bucket {signal.bucket!r} "
                    f"(pattern={signal.pattern_id})"
                )
                continue
            try:
                handler(signal)
                result.dispatched += 1
                bucket_counts[str(signal.bucket)] += 1
            except Exception as e:
                result.errors.append(
                    f"handler failed for {signal.pattern_id}: {e}"
                )
                logger.exception("dispatch handler failed")

        result.by_bucket = dict(bucket_counts)
        return result

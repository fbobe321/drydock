"""Failure classifier — Phase 2 keystone of the Sovereign v2 framework.

The classifier is the bridge between **observation** (admiral fires,
tool errors, loop-detection signals, session log patterns) and
**action** (harness PR, GraphRAG corpus tweak, Deep Noir vector
candidate, LoRA training data).

Without this layer the v2 modules (harness / GraphRAG / Deep Noir) are
isolated levers a human pulls. With it, every failure produces a
structured signal — `{bucket, evidence, suggested_action}` — that the
dispatchers can route to the right module automatically.

For v0 the classifier is rule-based: a registry of regex patterns,
each tagged with a bucket and an evidence-extraction template. v1+ can
swap to an LLM classifier behind the same `Classifier` interface; the
rest of the system never knows the difference.

Public surface:
    from drydock.core.classifier import (
        Bucket, FailureSignal, Classifier, classify_text, classify_lines,
    )
"""
from __future__ import annotations

from drydock.core.classifier.classifier import (
    Classifier,
    classify_lines,
    classify_text,
)
from drydock.core.classifier.dispatcher import (
    Dispatcher,
    DispatchResult,
    DispatchHandler,
    default_handler_for,
    make_jsonl_handler,
)
from drydock.core.classifier.signal import Bucket, FailureSignal

__all__ = [
    "Bucket",
    "Classifier",
    "DispatchHandler",
    "DispatchResult",
    "Dispatcher",
    "FailureSignal",
    "classify_lines",
    "classify_text",
    "default_handler_for",
    "make_jsonl_handler",
]

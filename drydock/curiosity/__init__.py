"""Curiosity Layer — engineered intellectual drive for drydock.

This module turns the SOVEREIGN_PRD §5.7 directive ("when you notice a gap,
you close it; when you encounter something unfamiliar, you investigate
before asserting") into observable signals + a feedback queue.

The classifier framework in `drydock.core.classifier` covers FAILURE
signals routed to fix-this-thing handlers. Curiosity is the complement:
LEARNING signals — gaps, surprises, and idle-exploration candidates that
deserve attention even when nothing has technically failed.

Public surface:

    from drydock.curiosity import (
        CuriosityItem, CuriosityKind,
        detect_gaps, score_surprise,
        enqueue, queue_path,
    )

Phase 1 (now): gap detection, surprise scoring stub, JSONL queue.
Phase 2 (next): consumer hook in autonomous_review.sh, retrieve auto-prefetch
                in agent_loop, system-prompt directive (already in gemma4.md).
Phase 3 (later): Deep Noir "exploration" vector candidate, idle-cycle worker.
"""
from __future__ import annotations

from drydock.curiosity.item import CuriosityItem, CuriosityKind
from drydock.curiosity.queue import enqueue, queue_path, read_recent
from drydock.curiosity.gap_detector import detect_gaps
from drydock.curiosity.surprise import score_surprise

__all__ = [
    "CuriosityItem",
    "CuriosityKind",
    "detect_gaps",
    "score_surprise",
    "enqueue",
    "queue_path",
    "read_recent",
]

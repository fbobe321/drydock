"""CuriosityItem — the unit of work in the curiosity queue."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from enum import StrEnum, auto


class CuriosityKind(StrEnum):
    """Why this item earned a spot in the queue."""

    # Named entity / identifier / paper title in a user message that the
    # model has no context for. Detected by `gap_detector`.
    UNKNOWN_TERM = auto()

    # The model asserted X; a retrieve / tool result / judge verdict
    # disagrees with X. Detected by `surprise.score_surprise`.
    EVIDENCE_CONFLICT = auto()

    # HLE question scored NO. Source of bulk learning signal —
    # `scripts/hle_eval.py` enqueues these.
    HLE_FAILURE = auto()

    # Idle exploration cycle picked up a classifier-queue pattern and
    # generated a hypothesis. Source of self-directed learning.
    IDLE_EXPLORATION = auto()


@dataclass
class CuriosityItem:
    """One learning signal worth ingesting / acting on."""

    kind: CuriosityKind
    term: str               # entity / phrase / question id this is about
    context: str            # surrounding text for the consumer to use
    source: str = ""        # "user_input", "hle:<id>", "session:<id>", etc.
    suggested_action: str = ""   # one-line hint for the consumer
    confidence: float = 1.0
    extra: dict[str, str] = field(default_factory=dict)
    # ts and id are populated by `enqueue` at write time, not by callers.
    # Left in the schema so consumers see the same shape they read back.
    ts: str = ""
    id: str = ""

    def to_jsonable(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    def fingerprint(self) -> str:
        """Stable hash over `kind + term + source` for dedup."""
        h = hashlib.sha256()
        h.update(self.kind.value.encode())
        h.update(b"|")
        h.update(self.term.encode())
        h.update(b"|")
        h.update(self.source.encode())
        return h.hexdigest()[:16]

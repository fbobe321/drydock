"""Failure signal data shapes — what the classifier emits.

A `FailureSignal` is the atomic output of one observation → one bucket
match. It carries enough context for a dispatcher to act:

- `bucket` — which module owns the fix (harness / retrieval / steering / etc.)
- `pattern_id` — stable id of the rule that fired (for stats + dedup)
- `evidence` — the line(s) / payload that triggered the match
- `suggested_action` — one-line human-readable next step
- `confidence` — 0.0–1.0; rule-based starts at fixed values, LLM v1
  produces calibrated scores
- `source`, `session_id`, `prompt_id` — provenance for grouping

Buckets are an enum so dispatchers can switch on them without typos.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum, auto


class Bucket(StrEnum):
    """The five primary failure-class buckets.

    Validated against the 11 patterns in MODEL_SHORTCOMINGS.md (see
    TRIAGE_v1.md). Every observed failure must classify into one of
    these — `OTHER` is the escape hatch that should stay rare; if it
    grows, the taxonomy needs another bucket."""
    HARNESS = auto()           # tool plumbing, prompt structure, write/edit safety
    RETRIEVAL = auto()         # missing context the model has no way to know
    STEERING = auto()          # behavioral priors (search-when-stuck, etc.)
    MODEL_PRIOR = auto()       # reasoning gaps — LoRA territory
    AMBIGUOUS_INPUT = auto()   # PRD / user prompt under-specified, not a model bug
    OTHER = auto()             # taxonomy-miss (should stay rare)


@dataclass
class FailureSignal:
    """One classified failure observation."""
    bucket: Bucket
    pattern_id: str
    evidence: str
    suggested_action: str
    confidence: float = 1.0
    source: str = ""        # filename / log type
    session_id: str = ""
    prompt_id: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def to_jsonable(self) -> dict:
        d = asdict(self)
        d["bucket"] = str(self.bucket)
        return d

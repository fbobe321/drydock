"""SteeringConfig — what to apply to a session.

A `SteeringConfig` says "for this session / request, apply these modes
at these scales." The harness builds it from explicit user intent
(`--steering secure-coding`) or from a classifier signal
(`steering-class failure detected → suggest mode X`). Multiple modes
can stack; each contributes its vector(s) at its own scale.

Default config is `SteeringConfig.disabled()` — no vectors applied.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class ModeSpec:
    """One steering mode in a config: which mode + scale override."""
    name: str               # mode name (e.g. "secure-coding")
    scale_override: float | None = None  # None => use manifest default


@dataclass
class SteeringConfig:
    """Per-session steering configuration. Frozen-by-convention; build
    a fresh one rather than mutating."""
    modes: list[ModeSpec] = field(default_factory=list)
    enabled: bool = True

    @classmethod
    def disabled(cls) -> "SteeringConfig":
        return cls(modes=[], enabled=False)

    @classmethod
    def from_mode_names(
        cls, names: Iterable[str], scales: dict[str, float] | None = None
    ) -> "SteeringConfig":
        """Build a config from a list of mode names and optional per-mode
        scale overrides. If a mode appears in `scales`, that scale wins;
        otherwise the manifest default is used at apply time."""
        scales = scales or {}
        return cls(modes=[
            ModeSpec(name=n, scale_override=scales.get(n)) for n in names
        ])

    def is_active(self) -> bool:
        return self.enabled and bool(self.modes)

    def mode_names(self) -> list[str]:
        return [m.name for m in self.modes]

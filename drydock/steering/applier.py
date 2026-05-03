"""SteeringApplier protocol — the seam where vectors meet inference.

The harness layer (agent_loop, request building) calls
`apply_steering(config, registry, applier)` before issuing a request.
The applier is the part that knows how to actually inject the vector
into the model's hidden state. Three implementations:

- `NullSteeringApplier` (default) — no-op. Used when no vectors are
  available, when the active model doesn't match any vector's
  target_model, or when the operator hasn't enabled steering. Lets the
  whole steering seam stay wired without changing inference behavior.
- `LogOnlySteeringApplier` — useful for development / smoke testing.
  Logs which vectors would be applied at which scales, then no-ops.
- `VllmSidecarSteeringApplier` (TODO) — the real backend that talks to
  vLLM via a sidecar process running the model under
  transformers + activation hooks. Not implemented in v0; this is the
  Phase-3+ hardening work.

The protocol returns a `SteeringDecision` so callers know what was
applied — useful for logging, evaluator grounding checks, and the
sandbox eval (`SteeringSandbox`) that diffs outputs with vs. without.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Protocol, runtime_checkable

from drydock.steering.config import SteeringConfig
from drydock.steering.registry import SteeringRegistry
from drydock.steering.vectors import Vector

logger = logging.getLogger(__name__)


@dataclass
class SteeringDecision:
    """Records what an applier did for one request. Used by the evaluator
    and for trip-log style reporting in autonomous_review."""
    config: SteeringConfig
    applied_vectors: list[Vector] = field(default_factory=list)
    skipped_reasons: list[str] = field(default_factory=list)
    applier_kind: str = "null"

    def is_noop(self) -> bool:
        return not self.applied_vectors

    def summary(self) -> str:
        if self.is_noop():
            reason = "; ".join(self.skipped_reasons) if self.skipped_reasons else "no modes selected"
            return f"steering: noop ({reason})"
        bits = [
            f"{v.manifest.name}@layer{v.manifest.layer}×{v.manifest.scale}"
            for v in self.applied_vectors
        ]
        return f"steering ({self.applier_kind}): {', '.join(bits)}"


@runtime_checkable
class SteeringApplier(Protocol):
    """Pluggable backend for actually applying a vector at inference."""
    kind: str

    def apply(
        self,
        vectors: Iterable[Vector],
        *,
        active_model: str,
    ) -> list[Vector]:
        """Apply each vector. Returns the subset actually applied (e.g.
        a backend may decline if a layer is out of range or the
        target_model doesn't match)."""
        ...


class NullSteeringApplier:
    """No-op applier — the safe default."""
    kind = "null"

    def apply(
        self,
        vectors: Iterable[Vector],
        *,
        active_model: str,
    ) -> list[Vector]:
        return []


class LogOnlySteeringApplier:
    """Logs what would be applied without changing inference."""
    kind = "log_only"

    def apply(
        self,
        vectors: Iterable[Vector],
        *,
        active_model: str,
    ) -> list[Vector]:
        out: list[Vector] = []
        for v in vectors:
            if not v.matches_model(active_model):
                logger.info(
                    "steering: skipping %s (target=%s, active=%s)",
                    v.manifest.name, v.manifest.target_model, active_model,
                )
                continue
            logger.info(
                "steering: would apply %s @ layer %d × %.3f",
                v.manifest.name, v.manifest.layer, v.manifest.scale,
            )
            out.append(v)
        return out


def apply_steering(
    config: SteeringConfig,
    registry: SteeringRegistry,
    applier: SteeringApplier,
    *,
    active_model: str,
) -> SteeringDecision:
    """Resolve the SteeringConfig against the registry, hand the
    matching vectors to the applier, and return what got applied.

    Never raises on missing modes / vectors — those become entries in
    `skipped_reasons` so the operator can see them in logs without
    breaking inference."""
    decision = SteeringDecision(config=config, applier_kind=applier.kind)
    if not config.is_active():
        decision.skipped_reasons.append("config disabled or empty")
        return decision

    vectors_to_try: list[Vector] = []
    for mode_spec in config.modes:
        loaded = registry.load_for_mode(mode_spec.name)
        if not loaded:
            decision.skipped_reasons.append(f"no vectors for mode {mode_spec.name!r}")
            continue
        for v in loaded:
            if mode_spec.scale_override is not None:
                # Carry the override on the manifest by replacing it.
                # Manifest is frozen, so build a fresh Vector with a new
                # manifest using dataclasses.replace.
                from dataclasses import replace
                v = Vector(
                    manifest=replace(v.manifest, scale=mode_spec.scale_override),
                    data=v.data,
                    payload_path=v.payload_path,
                )
            vectors_to_try.append(v)

    decision.applied_vectors = applier.apply(
        vectors_to_try, active_model=active_model
    )
    if not decision.applied_vectors and not decision.skipped_reasons:
        decision.skipped_reasons.append("applier accepted nothing")
    return decision

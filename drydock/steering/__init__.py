"""Deep Noir activation-steering scaffolding for Drydock Sovereign v2.

A first-class deployable module per the SOVEREIGN_PRD. Phase-3 deliverable.

Activation steering applies a learned direction vector to a model's
hidden state at a specific decoder layer to bias behavior — e.g. boost
"quote-source" directions in RAG flows, suppress "make-stuff-up"
directions for hallucination reduction. The vectors themselves are the
operator's research output (Deep Noir); this module is the **harness-side
infrastructure** that loads, registers, applies, and sandbox-evaluates
them.

This first cut deliberately does NOT patch vLLM's forward pass — that's
a research-integration step that lands when actual vectors arrive. What
it DOES provide:

1. A vector format (`.npy` payload + sidecar `.toml` manifest)
2. A registry for discovering and loading vectors from
   `~/.drydock/steering/vectors/<mode>/`
3. A `SteeringConfig` per session ("apply mode X at scale Y")
4. A pluggable `SteeringApplier` protocol that the inference adapter
   calls. The default `NullSteeringApplier` is a no-op so the seam can
   be wired without breaking inference.
5. A sandbox eval harness that runs a fixed prompt set with steering
   on/off and diffs outputs — the gating mechanism for promoting a
   new vector into a deployment.

Public surface:
    from drydock.steering import (
        SteeringConfig, SteeringRegistry, Vector,
        load_registry, apply_steering, NullSteeringApplier,
    )

CLI:
    python -m drydock.steering list
    python -m drydock.steering inspect <name>
    python -m drydock.steering eval <mode> --prompts <file>
"""
from __future__ import annotations

from drydock.steering.applier import (
    NullSteeringApplier,
    SteeringApplier,
    apply_steering,
)
from drydock.steering.config import SteeringConfig
from drydock.steering.registry import (
    SteeringRegistry,
    load_registry,
)
from drydock.steering.vectors import Vector, VectorManifest

__all__ = [
    "NullSteeringApplier",
    "SteeringApplier",
    "SteeringConfig",
    "SteeringRegistry",
    "Vector",
    "VectorManifest",
    "apply_steering",
    "load_registry",
]

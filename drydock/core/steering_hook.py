"""Agent-loop integration point for Deep Noir activation steering.

Lazy, env-gated, log-only by default. Goal: have the steering seam
wired into agent_loop **without changing inference behavior** until
real vectors and a non-Null applier exist. When Deep Noir vectors
arrive, swapping `LogOnlySteeringApplier` for the real backend (e.g.
a future `VllmSidecarSteeringApplier`) is a one-line change here.

How it activates:
- `DRYDOCK_STEERING_MODES=secure_coding,citation` enables steering with
  the named modes. If unset/empty, the hook is a complete no-op — the
  agent loop's call site sees `None` and skips the entire path.
- `DRYDOCK_STEERING_ROOT=/path/to/vectors` overrides the registry root
  (default: `~/.drydock/steering/vectors/`).

The hook caches the registry + applier once per process. Per-request
work is just `apply_steering(...)` — fast, and a no-op when the
registry is empty.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class _CachedSteering:
    config: object  # SteeringConfig — typed loosely to keep this module import-cheap
    registry: object
    applier: object


_LOCK = Lock()
_CACHE: _CachedSteering | None | str = "uninitialized"
# Sentinel values:
#   "uninitialized" — first call hasn't happened
#   None            — env not set or steering import failed; permanently disabled
#   _CachedSteering — active configuration, reused across calls


def _initialize() -> _CachedSteering | None:
    modes_env = os.environ.get("DRYDOCK_STEERING_MODES", "").strip()
    if not modes_env:
        return None
    mode_names = [m.strip() for m in modes_env.split(",") if m.strip()]
    if not mode_names:
        return None

    try:
        from drydock.steering import (
            LogOnlySteeringApplier,
            SteeringConfig,
            load_registry,
        )
    except Exception as e:
        logger.warning("steering: import failed (disabled): %s", e)
        return None

    root = os.environ.get("DRYDOCK_STEERING_ROOT") or None
    registry = load_registry(root)
    config = SteeringConfig.from_mode_names(mode_names)
    # Default applier is log-only — a real applier (vLLM sidecar etc.)
    # is the next-phase deliverable. The seam is the same either way.
    applier = LogOnlySteeringApplier()
    logger.info(
        "steering: enabled, modes=%s, registry=%s",
        mode_names,
        registry.root,
    )
    return _CachedSteering(config=config, registry=registry, applier=applier)


def _get() -> _CachedSteering | None:
    global _CACHE
    if _CACHE == "uninitialized":
        with _LOCK:
            if _CACHE == "uninitialized":
                _CACHE = _initialize()
    return _CACHE if isinstance(_CACHE, _CachedSteering) else None


def apply_for_request(active_model_name: str) -> str | None:
    """Run steering for one request. Returns a one-line summary the
    agent loop logs, or None if steering is disabled.

    Never raises — if the steering layer hits any error we log and
    return None, leaving inference behavior unchanged."""
    decision = decide_for_request(active_model_name)
    if decision is None:
        return None
    return decision.summary()


def decide_for_request(active_model_name: str):
    """Like apply_for_request but returns the full SteeringDecision so the
    caller can extract per-applier extras (e.g. logit_bias for the
    LogitBiasSteeringApplier).

    Returns None if steering is disabled or any error occurs — the
    caller should fall through to default sampling in that case."""
    cached = _get()
    if cached is None:
        return None
    try:
        from drydock.steering.applier import apply_steering
        decision = apply_steering(
            cached.config,
            cached.registry,
            cached.applier,
            active_model=active_model_name,
        )
        return decision
    except Exception as e:
        logger.warning("steering: apply failed (skipped): %s", e)
        return None


def logit_bias_for_request(active_model_name: str) -> dict[int, float]:
    """Convenience: get the merged logit_bias dict for the current
    request, or {} if the active applier doesn't produce one."""
    decision = decide_for_request(active_model_name)
    if decision is None or decision.applier_kind != "logit_bias":
        return {}
    try:
        from drydock.steering.applier import accumulate_logit_bias
        return accumulate_logit_bias(decision)
    except Exception as e:
        logger.warning("steering: logit_bias accumulation failed: %s", e)
        return {}


def reset_cache_for_tests() -> None:
    """Clear the module-level cache. Tests use this to swap env vars."""
    global _CACHE
    with _LOCK:
        _CACHE = "uninitialized"

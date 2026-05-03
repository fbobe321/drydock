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


class LogitBiasSteeringApplier:
    """Token-level steering — v0 working applier.

    Real activation steering modifies hidden states at a specific decoder
    layer; that requires a vLLM forward-pass patch and is the next-phase
    research-integration deliverable. THIS applier achieves a working
    end-to-end Deep Noir loop TODAY by mapping each accepted vector's
    metadata into vLLM's `logit_bias` SamplingParam — boosting or
    suppressing specific tokens in the model's output distribution.

    Vector format extension: this applier reads `tags.tokens_boost` and
    `tags.tokens_suppress` (lists of strings) from the manifest. The
    inference-side accumulator (in agent_loop or backend) calls
    `accumulate_logit_bias(decision)` to merge biases across all
    accepted vectors into a single `{token_id: float}` dict that vLLM
    consumes.

    LIMITATIONS — read this before using:
      - This is NOT activation steering. It changes the OUTPUT distribution,
        not the model's internal representation. Many Deep Noir effects
        (suppressing 'make-stuff-up' DIRECTIONS at layer 12) cannot be
        approximated by token boosting; behavior at the directional level
        requires the real applier.
      - Token-level effects are coarse: boosting "subprocess" doesn't make
        the model write secure code, just biases it toward that token when
        sampling.
      - Suitable for: vocabulary-level domain bias (legal-precision tokens,
        citation-mode tokens), known-bad-token suppression.
      - Not suitable for: hallucination-suppression in the abstract sense,
        chain-of-thought steering.

    The real applier (VllmSidecarSteeringApplier) is the planned successor.
    When it lands, swap one class behind the same SteeringApplier protocol.
    """
    kind = "logit_bias"

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
                "steering: accepting %s for logit-bias (scale=%.3f)",
                v.manifest.name, v.manifest.scale,
            )
            out.append(v)
        return out


def accumulate_logit_bias(
    decision: "SteeringDecision",
    *,
    tokenizer=None,
    max_bias: float = 12.0,
) -> dict[int, float]:
    """Merge each accepted vector's manifest tokens_boost/tokens_suppress
    lists into a single `{token_id: float}` dict for vLLM.

    Returns an empty dict when:
    - No tokenizer is available (caller passed None and we can't import
      transformers — common in headless test runs)
    - No vectors carry token-bias metadata
    - The applier kind is not 'logit_bias'

    Boost magnitude is `+scale * 4.0` clamped to `max_bias`; suppress is
    `-scale * 4.0`. The 4.0 multiplier is empirical; vLLM treats values
    above ~10 as near-deterministic.

    Token-bias metadata in the manifest's `[tags]` table:
        tokens_boost = ["subprocess", "shlex", "pathlib"]
        tokens_suppress = ["eval", "exec", "os.system"]

    A `_load_tokenizer()` helper attempts transformers import lazily; the
    caller can also pass a pre-loaded tokenizer to avoid repeat loads."""
    if decision.applier_kind != "logit_bias":
        return {}
    if not decision.applied_vectors:
        return {}

    tok = tokenizer or _load_tokenizer()
    if tok is None:
        return {}

    biases: dict[int, float] = {}
    for v in decision.applied_vectors:
        # Read token lists out of the manifest's extra fields. We stash
        # them at load time on a `tags` mirror — see VectorManifest /
        # vectors.py for how to extend that surface; here we accept them
        # via a shadow attr on the manifest if present.
        boosts: list[str] = list(getattr(v.manifest, "tokens_boost", ()) or ())
        suppress: list[str] = list(getattr(v.manifest, "tokens_suppress", ()) or ())
        scale = float(v.manifest.scale)
        for token_str in boosts:
            for tok_id in _tokenize_one(tok, token_str):
                biases[tok_id] = max(
                    -max_bias,
                    min(max_bias, biases.get(tok_id, 0.0) + scale * 4.0),
                )
        for token_str in suppress:
            for tok_id in _tokenize_one(tok, token_str):
                biases[tok_id] = max(
                    -max_bias,
                    min(max_bias, biases.get(tok_id, 0.0) - scale * 4.0),
                )
    return biases


def _load_tokenizer():
    """Lazy transformers tokenizer load. Returns None if transformers
    isn't installed — the applier degrades to noop in that case."""
    try:
        import transformers
    except ImportError:
        return None
    try:
        # Default to gemma4 — the v2 deployment target.
        return transformers.AutoTokenizer.from_pretrained(
            "google/gemma-4-26b-a4b-it", trust_remote_code=True
        )
    except Exception:
        # Tokenizer not available offline — fine, applier degrades to noop.
        return None


def _tokenize_one(tokenizer, text: str) -> list[int]:
    """Encode a single string to token ids, skipping special tokens."""
    try:
        ids = tokenizer.encode(text, add_special_tokens=False)
        return [int(i) for i in ids]
    except Exception:
        return []


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

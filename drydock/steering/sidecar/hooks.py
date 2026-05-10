"""Forward-hook scaffolding for activation-steering on Gemma 4.

Milestone 2 deliverable per DEEP_NOIR_PRD.md. The hook layer is the
load-bearing piece that turns the sidecar from "an OpenAI-compat
wrapper around transformers" into "an OpenAI-compat wrapper that can
inject per-layer residual-stream offsets per-request."

Architecture:

- Hooks are registered ONCE at manager construction. Every forward
  pass through every decoder layer goes through them. Cost when
  inactive: one ContextVar.get() returning None and an early return
  — negligible relative to a transformer forward pass.
- Per-request directives are dispatched via a `contextvars.ContextVar`.
  This is the right primitive for FastAPI: it propagates correctly
  across `await` boundaries and threadpool dispatch, so concurrent
  requests don't clobber each other's steering state.
- `model.generate()` is GPU-serialised by torch (one generate at a
  time per model), so contention happens at the CUDA layer regardless
  of how many requests arrive. The ContextVar still matters for
  correctness across the asyncio→threadpool hop FastAPI does for
  sync handlers.

M2 acceptance test:

    A request with no `X-Drydock-Steering` header and a request with
    `show_work@18×0.5` (and ANY mode/layer/scale) must return
    bit-identical completions on a deterministic seed, BECAUSE the
    M2 vector lookup returns a zero tensor. Proves the wiring works.

M3+ replaces the zero-vector lookup with a real one that pulls from
the registry; behavior changes only at that point.
"""
from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

from drydock.steering.sidecar.header_parser import SteeringDirective

logger = logging.getLogger(__name__)


# Module-level ContextVar so a request handler's `activate()` block
# is visible to the model.generate it wraps, even when generate dips
# into torch C++ that calls back into our Python hook.
_active_directives: contextvars.ContextVar[Optional["ActiveSteering"]] = (
    contextvars.ContextVar("drydock_steering_active", default=None)
)


# Vectors must be a torch.Tensor of shape (hidden_dim,). We type it as
# Any here to keep `import torch` out of this module's import path —
# the manager is constructed with a real model and torch is already
# imported by then.
VectorLookup = Callable[[str, int], Optional[Any]]


@dataclass(frozen=True)
class ActiveSteering:
    """Resolved per-layer steering for one in-flight request.

    `by_layer[idx]` is the list of `(scale, vector)` pairs to add to
    layer `idx`'s residual output. Multiple directives can target the
    same layer (the PRD allows comma-separated modes); they sum.
    """
    by_layer: dict[int, list[tuple[float, Any]]]

    def fired_layers(self) -> list[int]:
        return sorted(self.by_layer.keys())


def get_active_steering() -> Optional[ActiveSteering]:
    """Read the current ContextVar. Exposed for tests."""
    return _active_directives.get()


class SteeringHookManager:
    """Owns the lifecycle of per-layer forward hooks on a loaded model.

    Construct ONCE per model. The `vector_lookup` callable is invoked
    at directive-activation time, not per-forward — so the lookup can
    do non-trivial work (file reads, sha256 verification) without
    blocking inference.
    """

    def __init__(self, model: Any, vector_lookup: VectorLookup):
        self._model = model
        self._vector_lookup = vector_lookup
        self._handles: list[Any] = []
        self._layers = self._discover_layers(model)
        self._n_layers = len(self._layers)
        self._register_hooks()

    @staticmethod
    def _discover_layers(model: Any) -> Any:
        # transformers convention: decoder blocks live at
        # `model.model.layers` (Gemma, Llama, Mistral, …). Some legacy
        # archs put them at `model.layers` directly. Accept both.
        candidates = [
            getattr(getattr(model, "model", None), "layers", None),
            getattr(model, "layers", None),
        ]
        for c in candidates:
            if c is not None and hasattr(c, "__len__") and len(c) > 0:
                return c
        raise RuntimeError(
            "steering: cannot locate decoder layers — expected "
            f"model.model.layers or model.layers (got {type(model).__name__})"
        )

    @property
    def n_layers(self) -> int:
        return self._n_layers

    def _make_hook(self, layer_idx: int) -> Callable[..., Any]:
        def hook(_module: Any, _inputs: Any, output: Any) -> Any:
            active = _active_directives.get()
            if active is None:
                return output
            entries = active.by_layer.get(layer_idx)
            if not entries:
                return output

            # Decoder-layer outputs in transformers are typically a
            # tuple (hidden_states, present_kv, attn_weights, ...).
            # We only ever modify the hidden_states; everything else
            # rides through unchanged.
            if isinstance(output, tuple):
                hidden = output[0]
                rest: tuple[Any, ...] = output[1:]
            else:
                hidden = output
                rest = ()

            for scale, vector in entries:
                if vector is None or scale == 0.0:
                    continue
                # Match dtype/device of the residual stream — vectors
                # live on CPU as fp32 in the registry, residuals are
                # whatever the model picked (fp16/bf16 typically).
                v = vector.to(dtype=hidden.dtype, device=hidden.device)
                hidden = hidden + scale * v

            if rest:
                return (hidden,) + rest
            return hidden

        return hook

    def _register_hooks(self) -> None:
        for i, layer in enumerate(self._layers):
            handle = layer.register_forward_hook(self._make_hook(i))
            self._handles.append(handle)
        logger.info(
            "steering: registered %d forward hooks (one per decoder layer)",
            len(self._handles),
        )

    @contextmanager
    def activate(
        self, directives: list[SteeringDirective]
    ) -> Iterator[Optional[ActiveSteering]]:
        """Set the ContextVar for the duration of one generate() call.

        Resolves each directive's vector NOW (not in the hook hot
        path). Out-of-range layers and missing vectors are dropped
        with a warning, never raised — a malformed request should
        still serve a completion, just unsteered.
        """
        if not directives:
            yield None
            return

        by_layer: dict[int, list[tuple[float, Any]]] = {}
        for d in directives:
            if d.layer < 0 or d.layer >= self._n_layers:
                logger.warning(
                    "steering: directive %s layer out of range (0..%d), skipping",
                    d, self._n_layers - 1,
                )
                continue
            try:
                vec = self._vector_lookup(d.mode, d.layer)
            except Exception as e:
                logger.warning(
                    "steering: vector lookup raised for %s: %s — skipping", d, e
                )
                continue
            if vec is None:
                logger.warning(
                    "steering: no vector for mode=%r layer=%d — skipping",
                    d.mode, d.layer,
                )
                continue
            by_layer.setdefault(d.layer, []).append((d.scale, vec))

        if not by_layer:
            yield None
            return

        active = ActiveSteering(by_layer=by_layer)
        token = _active_directives.set(active)
        try:
            yield active
        finally:
            _active_directives.reset(token)

    def close(self) -> None:
        """Remove every hook. After close(), the manager is unusable."""
        for h in self._handles:
            try:
                h.remove()
            except Exception:
                pass
        self._handles.clear()


# --- Capture-mode hooks (Milestone 3) --------------------------------------
#
# The capture path is the read-only complement to SteeringHookManager: the
# hooks observe each layer's residual stream and record the chosen token
# position into a per-pass buffer, but they do not modify the output. This
# is what `drydock/steering/train/capture.py` uses to gather the
# (good_residuals, derailed_residuals) pairs M4 will turn into vectors.
#
# Same ContextVar pattern as the inject path so the same model can serve
# both modes (in different processes — capture is offline, inject is
# online — but the abstraction stays uniform).

_active_capture: contextvars.ContextVar[Optional["CaptureBuffer"]] = (
    contextvars.ContextVar("drydock_steering_capture", default=None)
)


@dataclass
class CaptureBuffer:
    """Per-forward-pass buffer holding one residual vector per layer.

    `position` is the token index to capture. -1 = last token (the
    most common pattern for steering-vector training: the model has
    seen the whole prompt+completion and the LAST hidden state is the
    cleanest read on whether it's currently in the "good" or
    "derailed" trajectory). 0 = first token, etc.

    Residuals land on CPU as fp32 — the hook eagerly detaches and
    casts so the per-pass buffer doesn't hold CUDA refs after the
    forward pass returns. That keeps GPU memory bounded across long
    capture runs.
    """
    position: int = -1
    residuals: dict[int, Any] = field(default_factory=dict)
    batch_index: int = 0    # which row of the batch to capture (default 0)

    def stack(self, n_layers: int) -> Any:
        """Stack into a (n_layers, hidden_dim) array. Missing layers
        are filled with zeros at the inferred hidden_dim. Returns a
        torch.Tensor; callers convert to numpy as needed."""
        import torch
        if not self.residuals:
            raise RuntimeError(
                "capture buffer is empty — did the forward pass run "
                "inside the `with mgr.capture():` block?"
            )
        sample = next(iter(self.residuals.values()))
        hidden_dim = sample.shape[-1]
        out = torch.zeros((n_layers, hidden_dim), dtype=torch.float32)
        for idx, vec in self.residuals.items():
            if 0 <= idx < n_layers:
                out[idx] = vec
        return out


def get_active_capture() -> Optional[CaptureBuffer]:
    """Read the current capture ContextVar. Exposed for tests."""
    return _active_capture.get()


class CaptureHookManager:
    """Owns per-layer forward hooks that record (do not modify) residuals.

    Concurrency: identical to SteeringHookManager — register once,
    dispatch per-pass via a ContextVar. Capture and steering can be
    used on the same process by constructing both managers; their
    hooks are independent.
    """

    def __init__(self, model: Any):
        self._model = model
        self._handles: list[Any] = []
        self._layers = SteeringHookManager._discover_layers(model)
        self._n_layers = len(self._layers)
        self._register_hooks()

    @property
    def n_layers(self) -> int:
        return self._n_layers

    def _make_hook(self, layer_idx: int) -> Callable[..., Any]:
        # Capture fires inside a torch forward pass, so torch is
        # already in sys.modules by then. Importing here keeps the
        # module-import path torch-free.
        import torch

        def hook(_module: Any, _inputs: Any, output: Any) -> Any:
            buf = _active_capture.get()
            if buf is None:
                return output
            hidden = output[0] if isinstance(output, tuple) else output
            # hidden: (batch, seq, hidden_dim). Pick the configured
            # batch row and token position. Detach + cast on the GPU
            # side, then move to CPU — minimizes the data crossing
            # the bus and avoids holding CUDA refs after the pass.
            if buf.batch_index >= hidden.shape[0]:
                logger.warning(
                    "capture: batch_index=%d out of range for shape %s",
                    buf.batch_index, tuple(hidden.shape),
                )
                return output
            row = hidden[buf.batch_index]
            seq_len = row.shape[0]
            pos = buf.position
            if pos < 0:
                pos = seq_len + pos
            if pos < 0 or pos >= seq_len:
                logger.warning(
                    "capture: position=%d out of range for seq_len=%d (layer=%d)",
                    buf.position, seq_len, layer_idx,
                )
                return output
            buf.residuals[layer_idx] = (
                row[pos].detach().to(dtype=torch.float32).cpu()
            )
            return output

        return hook

    def _register_hooks(self) -> None:
        for i, layer in enumerate(self._layers):
            handle = layer.register_forward_hook(self._make_hook(i))
            self._handles.append(handle)
        logger.info(
            "capture: registered %d forward hooks (read-only)",
            len(self._handles),
        )

    @contextmanager
    def capture(
        self, position: int = -1, batch_index: int = 0
    ) -> Iterator[CaptureBuffer]:
        """Activate the capture ContextVar for one forward pass.

        Returns the buffer; after the block exits, `buf.residuals`
        holds one tensor per layer that was reached during the pass.
        """
        buf = CaptureBuffer(position=position, batch_index=batch_index)
        token = _active_capture.set(buf)
        try:
            yield buf
        finally:
            _active_capture.reset(token)

    def close(self) -> None:
        for h in self._handles:
            try:
                h.remove()
            except Exception:
                pass
        self._handles.clear()

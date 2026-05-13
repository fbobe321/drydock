"""FastAPI server hosting a transformers-backed Gemma 4, OpenAI-compat shape.

Milestones 1+2 deliverable per DEEP_NOIR_PRD.md:
- M1: Loads Gemma 4 from a local path via `transformers.AutoModelForCausalLM`.
  Speaks `POST /v1/chat/completions` (non-streaming first cut) and
  `GET /v1/models` so the existing `llm_balancer.py` can route to it
  the same way it routes to llama.cpp.
- M2: Per-layer forward hooks via `SteeringHookManager`, dispatched
  per-request through a `ContextVar`. The `X-Drydock-Steering` header
  is parsed into directives, vectors are resolved (registry → zero
  fallback for M2 wiring check), and `model.generate` runs inside
  `hook_manager.activate(directives)`. With M2's zero-vector fallback,
  output is bit-identical with or without the header — that's the
  acceptance test for the wiring.

Lazy model load: the model is NOT loaded at import time. The first
inference request triggers `_load_model()` which holds a lock and
caches the result. This lets the FastAPI app start in <1s and reserve
its port even when GPU memory is tight; the heavy load only happens
when something actually wants steered inference.

Run:
    bash scripts/start_steering_sidecar.sh
or:
    /home/bobef/miniconda3/bin/python3 -m uvicorn \\
        drydock.steering.sidecar.server:app --host 0.0.0.0 --port 8002
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from drydock.steering.sidecar.header_parser import parse_header
from drydock.steering.sidecar.hooks import SteeringHookManager
from drydock.steering.sidecar.loader import load_model as _shared_load_model

logger = logging.getLogger(__name__)

# Reported model name in /v1/models. Matches what `start_gemma4.sh`
# reports so clients see a consistent name regardless of which
# backend serves them. Path/device live in `loader.py`.
_REPORTED_MODEL_NAME = os.environ.get(
    "DRYDOCK_STEERING_SIDECAR_MODEL_NAME", "gemma4"
)


# The model/tokenizer cache lives in `loader.py` so capture pipelines
# can share it. Server keeps only its own per-process state below.
_HOOK_MANAGER_LOCK = threading.Lock()
_HOOK_MANAGER: SteeringHookManager | None = None
_HOOK_MANAGER_INITIALIZED = False


def _build_vector_lookup(model: Any, registry: Any) -> Any:
    """Resolve `(mode, layer)` → `torch.Tensor | None`.

    M2 behavior: try the registry first; on miss return a zero tensor
    of `hidden_size`. Zero vectors mean the hook fires (proving the
    wiring works) but adds nothing to the residual stream — output
    is bit-identical to the no-header path.

    M3+ will replace the zero fallback with `return None` once real
    vectors land, so an unknown mode degrades gracefully to unsteered.
    """
    import io
    import numpy as np
    import torch

    hidden_dim = int(model.config.hidden_size)
    cache: dict[tuple[str, int], Any] = {}

    def lookup(mode: str, layer: int) -> Any:
        key = (mode, layer)
        if key in cache:
            return cache[key]

        tensor: Any = None
        try:
            vectors = registry.load_for_mode(mode) if registry is not None else []
        except Exception as e:
            logger.warning("steering: registry load failed for mode=%r: %s", mode, e)
            vectors = []

        match = None
        for v in vectors:
            if int(v.manifest.layer) == int(layer):
                match = v
                break

        if match is not None:
            try:
                arr = np.load(io.BytesIO(match.data))
                if arr.ndim != 1 or arr.shape[0] != hidden_dim:
                    logger.warning(
                        "steering: vector %s has shape %s, expected (%d,) — "
                        "falling back to zero",
                        match.manifest.name, arr.shape, hidden_dim,
                    )
                else:
                    tensor = torch.from_numpy(arr.astype(np.float32, copy=False))
            except Exception as e:
                logger.warning(
                    "steering: failed to decode vector %s: %s — falling back to zero",
                    match.manifest.name, e,
                )

        if tensor is None:
            # M2 wiring check: a zero vector at the right shape proves
            # the hook ran and the dtype/device handling is correct,
            # without changing the completion. Logged once per (mode, layer).
            logger.info(
                "steering: no vector for mode=%r layer=%d — using zero "
                "(M2 wiring check, expected before M4 ships real vectors)",
                mode, layer,
            )
            tensor = torch.zeros(hidden_dim, dtype=torch.float32)

        cache[key] = tensor
        return tensor

    return lookup


def _load_model() -> tuple[Any, Any]:
    """Lazy + thread-safe model load. Delegates to the shared loader,
    then attaches the steering hook manager once on first hit.

    Returns `(model, tokenizer)`. Raises RuntimeError on any underlying
    transformers/torch failure — the FastAPI handler converts that into
    a 503 so the balancer can fall back to llama.cpp.
    """
    model, tokenizer = _shared_load_model()
    global _HOOK_MANAGER, _HOOK_MANAGER_INITIALIZED
    if _HOOK_MANAGER_INITIALIZED:
        return model, tokenizer
    with _HOOK_MANAGER_LOCK:
        if _HOOK_MANAGER_INITIALIZED:
            return model, tokenizer
        try:
            from drydock.steering.registry import load_registry
            registry = load_registry()
            logger.info(
                "steering sidecar: registry loaded with %d modes",
                len(registry.list_modes()),
            )
        except Exception as e:
            logger.warning(
                "steering sidecar: registry unavailable (%s) — zero-vector fallback only",
                e,
            )
            registry = None
        try:
            _HOOK_MANAGER = SteeringHookManager(
                model, _build_vector_lookup(model, registry)
            )
        except Exception as e:
            # Hook installation failure must not stop us from serving
            # unsteered traffic — the sidecar still has a job.
            logger.error(
                "steering sidecar: hook manager init failed (%s) — serving unsteered",
                e,
            )
            _HOOK_MANAGER = None
        _HOOK_MANAGER_INITIALIZED = True
        return model, tokenizer


def _now() -> int:
    return int(time.time())


def prepare_chat_inputs(
    tokenizer: Any, messages: list[dict[str, Any]], *, device: Any = None
) -> dict[str, Any]:
    """Apply the tokenizer's chat template and return a dict that
    `model.generate(**inputs, ...)` can consume.

    transformers >=5 returns a BatchEncoding (dict-like) from
    `apply_chat_template`; older versions returned a bare Tensor. This
    helper normalizes both shapes to `{"input_ids": Tensor, ...}` and
    optionally moves the tensors onto `device`.

    Falls back to tokenize-then-encode when the template path doesn't
    accept `return_dict=True` or fails.
    """
    import torch

    try:
        inputs = tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
        )
    except TypeError:
        prompt_text = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False
        )
        inputs = tokenizer(prompt_text, return_tensors="pt")

    if isinstance(inputs, torch.Tensor):
        inputs = {"input_ids": inputs}
    else:
        inputs = dict(inputs)

    if device is not None:
        inputs = {
            k: (v.to(device) if isinstance(v, torch.Tensor) else v)
            for k, v in inputs.items()
        }

    if "input_ids" not in inputs:
        raise RuntimeError(
            f"prepare_chat_inputs: tokenizer returned {list(inputs.keys())!r} "
            "with no input_ids — chat template likely misconfigured"
        )

    return inputs


def build_app() -> FastAPI:
    app = FastAPI(
        title="Drydock Deep Noir Steering Sidecar",
        version="0.2.0-milestone2",
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        from drydock.steering.sidecar import loader as _loader
        return {
            "status": "ok",
            "model_loaded": _loader.is_loaded(),
            "model_path": _loader._model_path(),
            "hook_manager": _HOOK_MANAGER is not None,
        }

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": _REPORTED_MODEL_NAME,
                    "object": "model",
                    "created": _now(),
                    "owned_by": "drydock-steering-sidecar",
                }
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"invalid json: {e}")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise HTTPException(status_code=400, detail="missing messages[]")

        # M2: parse the steering header into directives. Malformed
        # entries are dropped by the parser, never raised.
        steering_header = request.headers.get("x-drydock-steering", "")
        directives = parse_header(steering_header)
        if steering_header:
            logger.info(
                "steering header: raw=%r parsed=%d directive(s)",
                steering_header, len(directives),
            )

        # Lazy load — pays the cost only on the first real request.
        try:
            model, tokenizer = _load_model()
        except RuntimeError as e:
            logger.error("model load failed: %s", e)
            return JSONResponse(
                status_code=503,
                content={"error": {"message": str(e), "type": "model_load_failed"}},
            )

        # Apply the chat template — see prepare_chat_inputs for the API
        # drift across transformers versions.
        import torch

        try:
            inputs = prepare_chat_inputs(
                tokenizer, messages, device=model.device
            )
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"chat template failed: {e}"
            )
        prompt_ids = inputs["input_ids"]
        max_new_tokens = int(payload.get("max_tokens") or 512)
        # Gemma 4 anti-loop sampling defaults — match what we ship in
        # DEFAULT_MODELS for the local model. Caller can override via
        # the JSON payload.
        temperature = float(payload.get("temperature", 1.0))
        top_p = float(payload.get("top_p", 0.95))
        top_k = int(payload.get("top_k", 40))
        do_sample = temperature > 0

        # M2: run generate inside the hook manager's activate() block
        # so the ContextVar is set for the whole forward pass. If no
        # manager (init failed) or no directives, this degrades to
        # plain generate.
        active_layers: list[int] = []
        t0 = time.perf_counter()
        with torch.no_grad():
            gen_kwargs = dict(
                max_new_tokens=max_new_tokens,
                do_sample=do_sample,
                temperature=temperature if do_sample else 1.0,
                top_p=top_p if do_sample else 1.0,
                top_k=top_k if do_sample else 0,
                pad_token_id=tokenizer.eos_token_id,
            )
            if _HOOK_MANAGER is not None and directives:
                with _HOOK_MANAGER.activate(directives) as active:
                    if active is not None:
                        active_layers = active.fired_layers()
                    output = model.generate(**inputs, **gen_kwargs)
            else:
                output = model.generate(**inputs, **gen_kwargs)
        gen_time = time.perf_counter() - t0

        # Decode only the newly-generated portion.
        new_tokens = output[0][prompt_ids.shape[-1]:]
        completion = tokenizer.decode(new_tokens, skip_special_tokens=True)
        prompt_token_count = int(prompt_ids.shape[-1])
        completion_token_count = int(new_tokens.shape[-1])

        logger.info(
            "completion: prompt=%d completion=%d gen=%.2fs",
            prompt_token_count, completion_token_count, gen_time,
        )

        response: dict[str, Any] = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": _now(),
            "model": _REPORTED_MODEL_NAME,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": completion,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_token_count,
                "completion_tokens": completion_token_count,
                "total_tokens": prompt_token_count + completion_token_count,
            },
        }
        # Echo the steering decision back so the M2 acceptance test
        # can assert "directives parsed AND fired" without needing
        # log scraping. Non-standard field, prefixed `drydock_`.
        if directives:
            response["drydock_steering"] = {
                "parsed": [
                    {"mode": d.mode, "layer": d.layer, "scale": d.scale}
                    for d in directives
                ],
                "fired_layers": active_layers,
                "applied": bool(active_layers),
            }
        return JSONResponse(content=response)

    return app


# uvicorn looks for `app` by default
app = build_app()

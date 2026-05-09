"""FastAPI server hosting a transformers-backed Gemma 4, OpenAI-compat shape.

Milestone 1 deliverable per DEEP_NOIR_PRD.md:
- Loads Gemma 4 from a local path via `transformers.AutoModelForCausalLM`.
- Speaks `POST /v1/chat/completions` (non-streaming first cut) and
  `GET /v1/models` so the existing `llm_balancer.py` can route to it
  the same way it routes to llama.cpp.
- NO forward hooks yet (Milestone 2). Returns identical outputs to
  llama.cpp on a smoke prompt — that's the M1 acceptance check.

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

logger = logging.getLogger(__name__)

# Default to the AWQ-4bit Gemma 4 weights (matches CLAUDE.md "active
# model" path). Override with DRYDOCK_STEERING_SIDECAR_MODEL_PATH.
_DEFAULT_MODEL_PATH = "/data3/Models/Gemma-4-26B-A4B-it-AWQ-4bit"
_MODEL_PATH = os.environ.get(
    "DRYDOCK_STEERING_SIDECAR_MODEL_PATH", _DEFAULT_MODEL_PATH
)
# transformers picks the device automatically when device_map="auto",
# but operators often want to pin to a single GPU to avoid sharing
# with llama.cpp. CUDA_VISIBLE_DEVICES handles that at the env layer.
_DEVICE_MAP = os.environ.get("DRYDOCK_STEERING_SIDECAR_DEVICE_MAP", "auto")
# Reported model name in /v1/models. Matches what `start_gemma4.sh`
# reports so clients see a consistent name regardless of which
# backend serves them.
_REPORTED_MODEL_NAME = os.environ.get(
    "DRYDOCK_STEERING_SIDECAR_MODEL_NAME", "gemma4"
)


_MODEL_LOCK = threading.Lock()
_MODEL = None        # type: ignore[var-annotated]
_TOKENIZER = None    # type: ignore[var-annotated]


def _load_model() -> tuple[Any, Any]:
    """Lazy + thread-safe model load. Cached after the first call.

    Returns `(model, tokenizer)`. Raises RuntimeError on any underlying
    transformers/torch failure — the FastAPI handler converts that into
    a 503 so the balancer can fall back to llama.cpp.
    """
    global _MODEL, _TOKENIZER
    if _MODEL is not None and _TOKENIZER is not None:
        return _MODEL, _TOKENIZER
    with _MODEL_LOCK:
        if _MODEL is not None and _TOKENIZER is not None:
            return _MODEL, _TOKENIZER
        logger.info(
            "steering sidecar: loading model from %s (device_map=%s)",
            _MODEL_PATH, _DEVICE_MAP,
        )
        t0 = time.perf_counter()
        try:
            import torch  # local import — only need it when actually loading
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as e:
            raise RuntimeError(
                f"steering sidecar: missing dependency ({e}). Install "
                "transformers + torch (CUDA build) to use the sidecar."
            ) from e

        try:
            tokenizer = AutoTokenizer.from_pretrained(
                _MODEL_PATH, trust_remote_code=True
            )
            model = AutoModelForCausalLM.from_pretrained(
                _MODEL_PATH,
                device_map=_DEVICE_MAP,
                torch_dtype="auto",      # let the quant_config drive dtype
                trust_remote_code=True,
            )
            model.eval()
        except Exception as e:
            raise RuntimeError(
                f"steering sidecar: model load failed: {type(e).__name__}: {e}"
            ) from e

        elapsed = time.perf_counter() - t0
        logger.info(
            "steering sidecar: model loaded in %.1fs (param dtype=%s)",
            elapsed,
            getattr(next(iter(model.parameters()), torch.tensor(0)), "dtype", "?"),
        )
        _MODEL = model
        _TOKENIZER = tokenizer
        return _MODEL, _TOKENIZER


def _now() -> int:
    return int(time.time())


def build_app() -> FastAPI:
    app = FastAPI(
        title="Drydock Deep Noir Steering Sidecar",
        version="0.1.0-milestone1",
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "model_loaded": _MODEL is not None,
            "model_path": _MODEL_PATH,
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

        # Milestone 1: ignore the steering header entirely. Just verify
        # we can serve a completion. Hooks land in M2.
        steering_header = request.headers.get("x-drydock-steering", "")
        if steering_header:
            logger.info(
                "steering header received but no hooks active in M1: %r",
                steering_header,
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

        # Apply the chat template — Gemma 4 has its own.
        try:
            prompt_ids = tokenizer.apply_chat_template(
                messages,
                add_generation_prompt=True,
                return_tensors="pt",
            )
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"chat template failed: {e}"
            )

        import torch

        prompt_ids = prompt_ids.to(model.device)
        max_new_tokens = int(payload.get("max_tokens") or 512)
        # Gemma 4 anti-loop sampling defaults — match what we ship in
        # DEFAULT_MODELS for the local model. Caller can override via
        # the JSON payload.
        temperature = float(payload.get("temperature", 1.0))
        top_p = float(payload.get("top_p", 0.95))
        top_k = int(payload.get("top_k", 40))
        do_sample = temperature > 0

        t0 = time.perf_counter()
        with torch.no_grad():
            output = model.generate(
                prompt_ids,
                max_new_tokens=max_new_tokens,
                do_sample=do_sample,
                temperature=temperature if do_sample else 1.0,
                top_p=top_p if do_sample else 1.0,
                top_k=top_k if do_sample else 0,
                pad_token_id=tokenizer.eos_token_id,
            )
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

        return JSONResponse(
            content={
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
        )

    return app


# uvicorn looks for `app` by default
app = build_app()

"""Lazy model + tokenizer loader for the steering sidecar.

Lifted out of `server.py` so non-HTTP callers (e.g. the offline
capture pipeline in `drydock.steering.train.capture`) can reuse the
same load path without dragging in FastAPI/uvicorn.

The cache is module-level — calling `load_model()` from anywhere in
the process returns the same `(model, tokenizer)` pair after the
first hit. Thread-safe via a single lock around the load.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULT_MODEL_PATH = "/data3/Models/Gemma-4-26B-A4B-it-AWQ-4bit"


def _model_path() -> str:
    return os.environ.get(
        "DRYDOCK_STEERING_SIDECAR_MODEL_PATH", _DEFAULT_MODEL_PATH
    )


def _device_map() -> str:
    return os.environ.get("DRYDOCK_STEERING_SIDECAR_DEVICE_MAP", "auto")


_MODEL_LOCK = threading.Lock()
_MODEL: Any = None
_TOKENIZER: Any = None


def is_loaded() -> bool:
    return _MODEL is not None and _TOKENIZER is not None


def load_model() -> tuple[Any, Any]:
    """Lazy + thread-safe model load. Cached after the first call.

    Raises RuntimeError on any underlying transformers/torch failure —
    callers convert that into whatever error shape they want (HTTP
    503 in the FastAPI handler, sys.exit in CLIs).
    """
    global _MODEL, _TOKENIZER
    if _MODEL is not None and _TOKENIZER is not None:
        return _MODEL, _TOKENIZER
    with _MODEL_LOCK:
        if _MODEL is not None and _TOKENIZER is not None:
            return _MODEL, _TOKENIZER
        path = _model_path()
        device_map = _device_map()
        logger.info(
            "steering sidecar: loading model from %s (device_map=%s)",
            path, device_map,
        )
        t0 = time.perf_counter()
        try:
            import torch  # noqa: F401  (used below)
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as e:
            raise RuntimeError(
                f"steering sidecar: missing dependency ({e}). Install "
                "transformers + torch (CUDA build) to use the sidecar."
            ) from e

        try:
            tokenizer = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
            model = AutoModelForCausalLM.from_pretrained(
                path,
                device_map=device_map,
                torch_dtype="auto",
                trust_remote_code=True,
            )
            model.eval()
        except Exception as e:
            raise RuntimeError(
                f"steering sidecar: model load failed: {type(e).__name__}: {e}"
            ) from e

        elapsed = time.perf_counter() - t0
        try:
            param_dtype = next(iter(model.parameters())).dtype
        except StopIteration:
            param_dtype = "?"
        logger.info(
            "steering sidecar: model loaded in %.1fs (param dtype=%s)",
            elapsed, param_dtype,
        )
        _MODEL = model
        _TOKENIZER = tokenizer
        return _MODEL, _TOKENIZER

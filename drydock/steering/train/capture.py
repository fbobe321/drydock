"""Capture residual streams from (prompt, completion) pairs.

Milestone 3 deliverable per DEEP_NOIR_PRD.md. This is the offline
data-collection step that produces the input to M4 vector training.

Pipeline:

    pairs.jsonl                  ← one JSON per line:
                                    {"prompt": "...",
                                     "completion": "...",
                                     "label": "good" | "derailed",
                                     "id": "optional-stable-id"}
        │
        │  python -m drydock.steering.train.capture \\
        │      --pairs pairs.jsonl --out captures.npz
        ▼
    captures.npz                 ← residuals[n_pairs, n_layers, hidden_dim],
                                    labels[n_pairs], ids[n_pairs]

The captured tensor at `(pair_i, layer_j, :)` is the residual stream
at the chosen token position (default: the LAST token of
`prompt + completion`) at the OUTPUT of decoder layer `j`. Shape and
dtype match what `SteeringHookManager` would inject into in M2 — so
M4 can compute `good_mean - derailed_mean` and the result is a
ready-to-inject vector with no dimension juggling.

Reuses `drydock.steering.sidecar.loader.load_model()`, so the same
weights serve both the inject sidecar and the capture pipeline. Run
this on a host where the AWQ-4bit model fits (≥ ~14 GB free VRAM).
The capture takes ~1–2s per pair on Gemma 4 26B-A4B.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("drydock.steering.train.capture")

VALID_LABELS = ("good", "derailed", "control", "neutral")


def _read_pairs(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(path.read_text().splitlines()):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            pair = json.loads(line)
        except json.JSONDecodeError as e:
            raise SystemExit(f"{path}:{i+1}: invalid JSON: {e}")
        if "prompt" not in pair or "completion" not in pair:
            raise SystemExit(
                f"{path}:{i+1}: pair missing required 'prompt'/'completion' fields"
            )
        if "label" not in pair:
            pair["label"] = "unknown"
        if pair["label"] not in VALID_LABELS:
            logger.warning(
                "%s:%d: unrecognized label %r (kept anyway)",
                path, i + 1, pair["label"],
            )
        if "id" not in pair:
            pair["id"] = f"{path.stem}:{i+1}"
        out.append(pair)
    return out


def _resolve_position(
    tokenizer: Any, prompt: str, completion: str, mode: str
) -> tuple[Any, int]:
    """Tokenize prompt+completion and return (input_ids, target_position).

    `mode`:
      - "last": capture the LAST token of (prompt + completion). Standard
        choice for steering-vector training.
      - "first_completion": capture the first token of `completion` —
        useful when comparing "what did the model condition on?" vs.
        "what did it commit to?" Diverges from the "last" pattern when
        completions are long.
      - "middle_completion": midpoint of the completion, in case the
        model's residual at the very last token is dominated by EOS
        prediction.
    """
    full = prompt + completion
    full_ids = tokenizer(full, return_tensors="pt", add_special_tokens=True)
    full_input = full_ids["input_ids"]
    n_full = full_input.shape[-1]
    if n_full == 0:
        raise ValueError("empty input after tokenization")

    if mode == "last":
        return full_input, n_full - 1

    prompt_ids = tokenizer(prompt, return_tensors="pt", add_special_tokens=True)
    n_prompt = prompt_ids["input_ids"].shape[-1]
    completion_len = max(1, n_full - n_prompt)

    if mode == "first_completion":
        pos = min(n_prompt, n_full - 1)
    elif mode == "middle_completion":
        pos = min(n_prompt + completion_len // 2, n_full - 1)
    else:
        raise ValueError(f"unknown position mode: {mode!r}")
    return full_input, pos


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="drydock.steering.train.capture",
        description=__doc__.split("\n")[0],
    )
    ap.add_argument(
        "--pairs", required=True, type=Path,
        help="JSONL file of {prompt, completion, label, id?} records",
    )
    ap.add_argument(
        "--out", required=True, type=Path,
        help="Output path for compressed .npz",
    )
    ap.add_argument(
        "--position",
        choices=("last", "first_completion", "middle_completion"),
        default="last",
        help="Which token to capture (default: last)",
    )
    ap.add_argument(
        "--max-pairs", type=int, default=None,
        help="Cap on pairs to process (for smoke runs)",
    )
    ap.add_argument(
        "--max-length", type=int, default=4096,
        help="Truncate inputs longer than this many tokens (default: 4096)",
    )
    ap.add_argument(
        "--log-level", default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    pairs = _read_pairs(args.pairs)
    if args.max_pairs is not None:
        pairs = pairs[: args.max_pairs]
    if not pairs:
        logger.error("no pairs to process")
        return 2
    logger.info("loaded %d pairs from %s", len(pairs), args.pairs)

    # Lazy imports so `--help` doesn't pay torch's startup cost.
    import numpy as np
    import torch
    from drydock.steering.sidecar.hooks import CaptureHookManager
    from drydock.steering.sidecar.loader import load_model

    try:
        model, tokenizer = load_model()
    except RuntimeError as e:
        logger.error("model load failed: %s", e)
        return 3

    mgr = CaptureHookManager(model)
    n_layers = mgr.n_layers
    hidden_dim = int(model.config.hidden_size)
    logger.info(
        "capture manager: n_layers=%d hidden_dim=%d", n_layers, hidden_dim
    )

    residuals = np.zeros((len(pairs), n_layers, hidden_dim), dtype=np.float32)
    labels = np.array([p["label"] for p in pairs], dtype=object)
    ids = np.array([p["id"] for p in pairs], dtype=object)
    skipped: list[tuple[str, str]] = []

    t0 = time.perf_counter()
    for i, pair in enumerate(pairs):
        try:
            input_ids, pos = _resolve_position(
                tokenizer, pair["prompt"], pair["completion"], args.position
            )
        except Exception as e:
            logger.warning("pair %s: tokenize failed (%s) — skipping", pair["id"], e)
            skipped.append((pair["id"], f"tokenize: {e}"))
            continue

        if input_ids.shape[-1] > args.max_length:
            # Truncate from the LEFT — keep the end of the input,
            # which is what the steering position lives at.
            input_ids = input_ids[..., -args.max_length:]
            pos = min(pos, input_ids.shape[-1] - 1)

        input_ids = input_ids.to(model.device)
        try:
            with torch.no_grad(), mgr.capture(position=pos) as buf:
                model(input_ids)
            stacked = buf.stack(n_layers).numpy()
        except Exception as e:
            logger.warning(
                "pair %s: forward/capture failed (%s) — skipping", pair["id"], e
            )
            skipped.append((pair["id"], f"forward: {e}"))
            continue

        residuals[i] = stacked
        if (i + 1) % 10 == 0 or i == 0:
            elapsed = time.perf_counter() - t0
            rate = (i + 1) / max(elapsed, 1e-6)
            logger.info(
                "[%d/%d] last_label=%s rate=%.2f pairs/s",
                i + 1, len(pairs), pair["label"], rate,
            )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "n_pairs": len(pairs),
        "n_layers": n_layers,
        "hidden_dim": hidden_dim,
        "position_mode": args.position,
        "max_length": args.max_length,
        "model_path": str(getattr(model, "name_or_path", "unknown")),
        "skipped": skipped,
        "schema_version": 1,
    }
    np.savez_compressed(
        args.out,
        residuals=residuals,
        labels=labels,
        ids=ids,
        meta=np.array(json.dumps(meta), dtype=object),
    )
    logger.info(
        "saved %s shape=%s skipped=%d total=%.1fs",
        args.out, residuals.shape, len(skipped), time.perf_counter() - t0,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

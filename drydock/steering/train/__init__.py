"""Vector-training pipeline (Deep Noir M3+).

This package owns the offline side of the steering loop:

- `capture.py`        — run (prompt, completion) pairs through the
                        sidecar's model with capture-mode hooks and
                        save residual streams to a .npz.
- `compute_vector.py` (M4) — turn captured residuals into per-layer
                        steering vectors via good_mean - derailed_mean.
- `extract_pairs.py`  (M4) — turn admiral_history traces into the
                        `pairs.jsonl` format `capture.py` consumes.
"""

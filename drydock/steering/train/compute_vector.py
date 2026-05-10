"""Train per-layer steering vectors from captured residual streams.

Milestone 4 deliverable per DEEP_NOIR_PRD.md. Closes the offline
training loop:

    pairs.jsonl --capture--> captures.npz --compute_vector--> vectors/

Algorithm (v1, "any non-zero direction works" per the PRD):

    For each requested layer L:
        good     = residuals[labels == "good",     L, :]   # (n_good, D)
        derailed = residuals[labels == "derailed", L, :]   # (n_derailed, D)
        v_L      = good.mean(axis=0) - derailed.mean(axis=0)   # (D,)
        # optional: v_L = v_L / np.linalg.norm(v_L)            # unit-norm

The result is a layer-aligned residual-stream offset that, when added
at inference time, nudges the hidden state toward the "good" centroid
and away from the "derailed" one. The applier's `scale` then controls
strength at request time (per the M2 header format).

Output layout (matches `drydock/steering/registry.py` convention):

    <out>/
        <mode>/
            <mode>_layer<L>.npy     # float32, shape (hidden_dim,)
            <mode>_layer<L>.toml    # VectorManifest

The manifest's `sha256` is computed against the .npy bytes after
write, so `Vector.load()` integrity checks pass round-trip.

Known limits (deferred to M5+):
- Equal-weight mean. No outlier filtering, no PCA, no per-token
  weighting. The PRD's M4 explicitly accepts a v0 of "any non-zero
  direction works" before iterating.
- No cross-validation split. Whole capture set goes into the
  centroid; M5's eval pass is what catches overfit.
- No multi-class extension (good vs everything-else). M4 supports
  exactly two contrastive labels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("drydock.steering.train.compute_vector")


@dataclass(frozen=True)
class VectorComputeResult:
    """One trained vector + the metadata needed to write its manifest."""
    mode: str
    layer: int
    hidden_dim: int
    vector: Any            # numpy.ndarray, shape (hidden_dim,), float32
    n_good: int
    n_derailed: int
    norm: float            # L2 norm BEFORE optional re-normalization
    normalized: bool

    def manifest_dict(self, *, target_model: str, scale: float, provenance: str) -> dict[str, Any]:
        """Build the dict that VectorManifest.from_toml_dict expects.

        sha256 is filled by the writer after the .npy bytes are flushed
        to disk — only the on-disk bytes are integrity-checked, so we
        can't pre-compute it from a numpy array (npy headers depend on
        numpy version)."""
        name = f"{self.mode}_layer{self.layer}"
        return {
            "vector": {
                "name": name,
                "description": (
                    f"Activation-steering vector for mode={self.mode!r} "
                    f"trained from {self.n_good} good vs "
                    f"{self.n_derailed} derailed residuals at layer "
                    f"{self.layer}. {'Unit-normalized.' if self.normalized else 'Raw mean-diff.'}"
                ),
                "layer": self.layer,
                "scale": scale,
                "target_model": target_model,
                "hidden_dim": self.hidden_dim,
                "sha256": "PLACEHOLDER",  # filled by writer
                "research_provenance": provenance,
            },
            "tags": {
                "mode": [self.mode],
                "family": "activation_diff_v1",
            },
        }


def _parse_layers(spec: str, n_layers: int) -> list[int]:
    """Parse `--layers` arg. Accepts '18', '16,17,18', '14-22', 'all'."""
    spec = spec.strip().lower()
    if spec in ("all", "*"):
        return list(range(n_layers))
    out: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo_s, hi_s = chunk.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if lo > hi:
                raise ValueError(f"layer range {chunk!r} is reversed")
            out.extend(range(lo, hi + 1))
        else:
            out.append(int(chunk))
    seen: set[int] = set()
    deduped: list[int] = []
    for L in out:
        if L not in seen:
            seen.add(L)
            deduped.append(L)
    bad = [L for L in deduped if L < 0 or L >= n_layers]
    if bad:
        raise ValueError(
            f"layers {bad} out of range (capture has 0..{n_layers - 1})"
        )
    return deduped


def compute_vectors(
    captures_path: Path,
    *,
    mode: str,
    layers: str = "all",
    good_label: str = "good",
    derailed_label: str = "derailed",
    normalize: bool = False,
) -> list[VectorComputeResult]:
    """Pure compute step — no disk writes, returns results in memory.

    Exposed as an importable function so tests + future programmatic
    callers (e.g. an HLE-driven layer sweep) can use it without
    shelling out.
    """
    import numpy as np

    data = np.load(captures_path, allow_pickle=True)
    residuals = data["residuals"]              # (N, n_layers, D)
    labels = np.array([str(s) for s in data["labels"]])
    if residuals.ndim != 3:
        raise ValueError(
            f"residuals must be 3D (n_pairs, n_layers, hidden_dim); got {residuals.shape}"
        )
    n_pairs, n_layers, hidden_dim = residuals.shape
    logger.info(
        "captures: %d pairs × %d layers × %d hidden_dim", n_pairs, n_layers, hidden_dim
    )

    good_mask = labels == good_label
    derailed_mask = labels == derailed_label
    n_good = int(good_mask.sum())
    n_derailed = int(derailed_mask.sum())
    if n_good == 0 or n_derailed == 0:
        raise ValueError(
            f"need at least one {good_label!r} and one {derailed_label!r} pair; "
            f"got {n_good} good, {n_derailed} derailed (label distribution: "
            f"{dict(zip(*np.unique(labels, return_counts=True)))})"
        )

    target_layers = _parse_layers(layers, n_layers)
    logger.info(
        "training %s vectors for mode=%r at layers %s (good=%d, derailed=%d)",
        len(target_layers), mode, target_layers, n_good, n_derailed,
    )

    results: list[VectorComputeResult] = []
    for L in target_layers:
        good_mean = residuals[good_mask, L, :].mean(axis=0)
        derailed_mean = residuals[derailed_mask, L, :].mean(axis=0)
        v = (good_mean - derailed_mean).astype(np.float32)
        norm = float(np.linalg.norm(v))
        if normalize:
            if norm < 1e-8:
                logger.warning(
                    "layer %d: vector norm %.2e ~= 0; cannot normalize. Keeping raw.",
                    L, norm,
                )
            else:
                v = (v / norm).astype(np.float32)
        results.append(
            VectorComputeResult(
                mode=mode,
                layer=int(L),
                hidden_dim=int(hidden_dim),
                vector=v,
                n_good=n_good,
                n_derailed=n_derailed,
                norm=norm,
                normalized=normalize and norm >= 1e-8,
            )
        )
        logger.info(
            "layer %2d: ||v||=%.4f%s",
            L, norm, " (normalized)" if (normalize and norm >= 1e-8) else "",
        )
    return results


def write_vectors(
    results: list[VectorComputeResult],
    out_root: Path,
    *,
    target_model: str,
    scale: float,
    provenance: str,
) -> list[Path]:
    """Serialise results to disk. Returns the manifest paths written."""
    import numpy as np

    written: list[Path] = []
    out_dir = out_root / results[0].mode
    out_dir.mkdir(parents=True, exist_ok=True)

    for r in results:
        npy_path = out_dir / f"{r.mode}_layer{r.layer}.npy"
        toml_path = out_dir / f"{r.mode}_layer{r.layer}.toml"
        np.save(npy_path, r.vector, allow_pickle=False)
        sha = hashlib.sha256(npy_path.read_bytes()).hexdigest()
        manifest = r.manifest_dict(
            target_model=target_model, scale=scale, provenance=provenance
        )
        manifest["vector"]["sha256"] = sha
        toml_path.write_text(_render_toml(manifest))
        written.append(toml_path)
        logger.info(
            "wrote %s (sha256=%s…)", toml_path.relative_to(out_root), sha[:12]
        )
    return written


def _render_toml(data: dict[str, Any]) -> str:
    """Minimal TOML writer for our manifest shape — three top-level
    tables ([vector], [tags]) with a small set of scalar/list values.
    Keeps the formatting stable across tomllib versions and avoids a
    runtime dep on tomli_w."""
    out: list[str] = []

    def fmt_value(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, float):
            # Use repr to preserve exact value for round-trip (1.0 → "1.0")
            return repr(v)
        if isinstance(v, str):
            return _quote(v)
        if isinstance(v, list):
            return "[" + ", ".join(fmt_value(x) for x in v) + "]"
        raise TypeError(f"unsupported manifest value type: {type(v).__name__}")

    for table_name in ("vector", "tags"):
        if table_name not in data:
            continue
        out.append(f"[{table_name}]")
        for key, value in data[table_name].items():
            out.append(f"{key} = {fmt_value(value)}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _quote(s: str) -> str:
    if "\n" in s or '"""' in s:
        # Multi-line string. Use triple-quoted, escape backslashes.
        escaped = s.replace("\\", "\\\\").replace('"""', '\\"""')
        return f'"""{escaped}"""'
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="drydock.steering.train.compute_vector",
        description=__doc__.split("\n")[0],
    )
    ap.add_argument(
        "--captures", required=True, type=Path,
        help=".npz produced by drydock.steering.train.capture",
    )
    ap.add_argument("--mode", required=True, help="Steering mode name (e.g. show_work)")
    ap.add_argument(
        "--layers", default="all",
        help="Comma/range spec ('18', '16,17,18', '16-20', 'all'). Default: all",
    )
    ap.add_argument(
        "--target-model", default="gemma4-26b-a4b",
        help="Target model name baked into each manifest (default: gemma4-26b-a4b)",
    )
    ap.add_argument(
        "--scale", type=float, default=0.5,
        help="Default scale baked into each manifest (default: 0.5)",
    )
    ap.add_argument(
        "--out", type=Path,
        default=Path.home() / ".drydock" / "steering" / "vectors",
        help="Vector registry root (default: ~/.drydock/steering/vectors)",
    )
    ap.add_argument(
        "--good-label", default="good",
        help="Label string for the positive class (default: good)",
    )
    ap.add_argument(
        "--derailed-label", default="derailed",
        help="Label string for the negative class (default: derailed)",
    )
    ap.add_argument(
        "--normalize", action="store_true",
        help="Unit-normalize each per-layer vector before writing",
    )
    ap.add_argument(
        "--provenance", default="",
        help="Free-form provenance string (defaults to a generated one)",
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

    if not args.captures.is_file():
        raise SystemExit(f"--captures path not found: {args.captures}")

    try:
        results = compute_vectors(
            args.captures,
            mode=args.mode,
            layers=args.layers,
            good_label=args.good_label,
            derailed_label=args.derailed_label,
            normalize=args.normalize,
        )
    except ValueError as e:
        logger.error("compute_vectors failed: %s", e)
        return 2

    provenance = args.provenance or (
        f"drydock M4 train: {args.captures.name} "
        f"(mode={args.mode}, layers={args.layers}, "
        f"normalize={args.normalize})"
    )
    written = write_vectors(
        results, args.out,
        target_model=args.target_model,
        scale=args.scale,
        provenance=provenance,
    )
    print(json.dumps(
        {
            "mode": args.mode,
            "layers": [r.layer for r in results],
            "n_good": results[0].n_good,
            "n_derailed": results[0].n_derailed,
            "manifests": [str(p) for p in written],
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())

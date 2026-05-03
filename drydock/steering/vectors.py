"""Vector format + manifest loader.

A steering vector is a 1-D tensor that lives at one decoder layer of the
target model. We store it as a `.npy` file (so it loads with stdlib +
numpy, no torch dep at the harness layer) plus a sidecar manifest that
records every detail an applier needs:

    vectors/secure_coding/v1.npy        # the tensor
    vectors/secure_coding/v1.toml       # the manifest

Manifest schema:

    [vector]
    name = "secure_coding_v1"
    description = "Suppress eval/exec/shell-injection prone outputs."
    layer = 18                  # 0-based decoder layer index
    scale = 0.6                 # default strength when this vector
                                # is selected by a SteeringConfig
    target_model = "gemma4-26b-a4b"
    hidden_dim = 4096
    sha256 = "abcd1234..."       # of the .npy bytes — integrity check
    research_provenance = "Deep Noir 2026-04-21 run 047"

    [tags]
    mode = ["secure-coding"]
    family = "suppression"

The harness never trusts a vector whose `target_model` doesn't match
the active model, or whose sha256 doesn't match the loaded bytes.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib   # py311+
except ImportError:  # pragma: no cover - py310 fallback
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class VectorManifest:
    """Metadata describing one steering vector. Fully serialisable."""
    name: str
    description: str
    layer: int
    scale: float
    target_model: str
    hidden_dim: int
    sha256: str
    research_provenance: str = ""
    mode_tags: tuple[str, ...] = ()
    family: str = ""

    @classmethod
    def from_toml_dict(cls, data: dict[str, Any]) -> "VectorManifest":
        v = data.get("vector", {})
        tags = data.get("tags", {})
        modes = tags.get("mode", []) if isinstance(tags, dict) else []
        if not isinstance(modes, list):
            modes = [modes]
        return cls(
            name=str(v["name"]),
            description=str(v.get("description", "")),
            layer=int(v["layer"]),
            scale=float(v.get("scale", 1.0)),
            target_model=str(v["target_model"]),
            hidden_dim=int(v["hidden_dim"]),
            sha256=str(v["sha256"]).lower(),
            research_provenance=str(v.get("research_provenance", "")),
            mode_tags=tuple(str(m) for m in modes),
            family=str(tags.get("family", "")) if isinstance(tags, dict) else "",
        )


class VectorIntegrityError(RuntimeError):
    """Raised when a vector's sha256 doesn't match the manifest."""


@dataclass
class Vector:
    """A steering vector + its manifest, ready for an applier to use.

    `data` is held as bytes here so the harness layer doesn't pull
    numpy in. Appliers that actually patch a model load `data` into
    whatever tensor format their backend wants (numpy, torch, jax)."""
    manifest: VectorManifest
    data: bytes
    payload_path: Path

    @classmethod
    def load(cls, manifest_path: str | Path) -> "Vector":
        """Load (manifest.toml, payload.npy) pair. Verifies sha256."""
        manifest_path = Path(manifest_path)
        with manifest_path.open("rb") as f:
            manifest = VectorManifest.from_toml_dict(tomllib.load(f))

        payload_path = manifest_path.with_suffix(".npy")
        if not payload_path.is_file():
            # Allow the manifest to be co-located with a differently-
            # named payload if needed — fall back to <stem>.npy.
            payload_path = manifest_path.parent / f"{manifest.name}.npy"
        if not payload_path.is_file():
            raise FileNotFoundError(
                f"vector payload not found beside {manifest_path}"
            )

        data = payload_path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != manifest.sha256:
            raise VectorIntegrityError(
                f"sha256 mismatch for {manifest.name}: "
                f"manifest={manifest.sha256} actual={actual}"
            )

        return cls(manifest=manifest, data=data, payload_path=payload_path)

    def matches_model(self, model_name: str) -> bool:
        """Loose match: the manifest's target_model must be a prefix of
        or equal to the active model name. Lets a vector trained on
        'gemma4-26b-a4b' apply to 'gemma4-26b-a4b-it' etc."""
        return model_name.startswith(self.manifest.target_model) or (
            self.manifest.target_model.startswith(model_name)
        )


def compute_sha256(payload_path: str | Path) -> str:
    """Helper for vector authors: compute the sha256 to put in the
    manifest after dropping in a new .npy file."""
    return hashlib.sha256(Path(payload_path).read_bytes()).hexdigest()

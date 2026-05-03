"""Vector registry — discover vectors on disk, group by mode.

Vectors live under a root directory (default: ~/.drydock/steering/vectors/).
Convention:

    <root>/
        secure_coding/
            v1.toml + v1.npy
            v2.toml + v2.npy
        anti_hallucination/
            v1.toml + v1.npy
        citation_boost/
            v1.toml + v1.npy

The registry walks the root, loads every (toml, npy) pair, and groups
them by their declared `mode_tags`. Modes named on the disk directory
become valid targets even before tags are read — the directory name
is the canonical mode key for `SteeringConfig`.

The registry tolerates broken / missing payload files: it logs and
skips, never raises during discovery. Callers select a vector by mode
name; only at that point do integrity checks fire (sha256 match).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from drydock.steering.vectors import Vector, VectorIntegrityError, VectorManifest

logger = logging.getLogger(__name__)


def _default_root() -> Path:
    return Path.home() / ".drydock" / "steering" / "vectors"


@dataclass
class _DiscoveredVector:
    manifest_path: Path
    manifest: VectorManifest
    mode_dir: str    # the on-disk parent directory name


@dataclass
class SteeringRegistry:
    """In-memory registry of discoverable vectors.

    Discovery is cheap (toml parse, no payload read). Loading is
    explicit (`load_vector`) so we only sha256-verify on demand.
    """
    root: Path
    discovered: list[_DiscoveredVector] = field(default_factory=list)

    def list_modes(self) -> list[str]:
        """Distinct mode names — directory names + manifest tags, sorted."""
        modes: set[str] = set()
        for d in self.discovered:
            modes.add(d.mode_dir)
            modes.update(d.manifest.mode_tags)
        return sorted(modes)

    def vectors_for_mode(self, mode: str) -> list[VectorManifest]:
        return [
            d.manifest
            for d in self.discovered
            if d.mode_dir == mode or mode in d.manifest.mode_tags
        ]

    def find_by_name(self, name: str) -> VectorManifest | None:
        for d in self.discovered:
            if d.manifest.name == name:
                return d.manifest
        return None

    def load_vector(self, name: str) -> Vector:
        """Load + verify the vector with the given manifest name."""
        for d in self.discovered:
            if d.manifest.name == name:
                return Vector.load(d.manifest_path)
        raise KeyError(f"vector not found in registry: {name!r}")

    def load_for_mode(self, mode: str) -> list[Vector]:
        """Load all vectors associated with `mode`. Skips vectors whose
        sha256 doesn't match (logged), so a corrupt file doesn't break
        a deployment that has multiple vectors per mode."""
        manifests = self.vectors_for_mode(mode)
        loaded: list[Vector] = []
        for m in manifests:
            try:
                loaded.append(self.load_vector(m.name))
            except VectorIntegrityError as e:
                logger.error("vector integrity failed for %s: %s", m.name, e)
            except FileNotFoundError as e:
                logger.error("vector payload missing for %s: %s", m.name, e)
        return loaded


def load_registry(root: str | Path | None = None) -> SteeringRegistry:
    """Discover all vectors under `root` (default: ~/.drydock/steering/vectors).

    Returns an empty registry if the root doesn't exist (a deployment
    with no vectors yet is a valid state — the harness is the same
    code path with or without steering).
    """
    root_path = Path(root) if root is not None else _default_root()
    discovered: list[_DiscoveredVector] = []
    if not root_path.is_dir():
        return SteeringRegistry(root=root_path, discovered=discovered)

    for mode_dir in sorted(p for p in root_path.iterdir() if p.is_dir()):
        for manifest_path in sorted(mode_dir.glob("*.toml")):
            try:
                import tomllib
                with manifest_path.open("rb") as f:
                    manifest = VectorManifest.from_toml_dict(tomllib.load(f))
            except Exception as e:
                logger.warning(
                    "skipping invalid vector manifest %s: %s", manifest_path, e
                )
                continue
            discovered.append(
                _DiscoveredVector(
                    manifest_path=manifest_path,
                    manifest=manifest,
                    mode_dir=mode_dir.name,
                )
            )

    return SteeringRegistry(root=root_path, discovered=discovered)

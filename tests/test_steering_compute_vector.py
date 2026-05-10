"""M4 vector-training tests + the M3→M4→M2 round-trip.

Three layers of coverage:

1. Pure compute (`compute_vectors`): correct math (good_mean -
   derailed_mean), layer spec parsing, unit-normalization toggle,
   error paths (no good pairs, no derailed pairs, layers out of
   range).
2. Disk format (`write_vectors`): produces (.npy, .toml) pairs that
   `Vector.load()` accepts (sha256 matches), readable by tomllib,
   round-trips back through `VectorManifest.from_toml_dict`.
3. End-to-end M3→M4→M2: synthesise a captures.npz with a known
   diff vector, run compute_vector, drop into a temp registry,
   load via SteeringRegistry, and verify SteeringHookManager's
   M2 lookup path picks up the real vector instead of the
   zero fallback.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
np = pytest.importorskip("numpy")
import torch.nn as nn  # noqa: E402

from drydock.steering.train.compute_vector import (  # noqa: E402
    _parse_layers,
    compute_vectors,
    write_vectors,
)
from drydock.steering.vectors import Vector, VectorManifest  # noqa: E402
from drydock.steering.registry import load_registry  # noqa: E402
from drydock.steering.sidecar.hooks import (  # noqa: E402
    SteeringHookManager,
)
from drydock.steering.sidecar.header_parser import SteeringDirective  # noqa: E402


# --- helpers ---------------------------------------------------------------


def _make_captures_npz(
    tmp_path: Path,
    n_good: int = 5,
    n_derailed: int = 5,
    n_layers: int = 4,
    hidden_dim: int = 8,
    *,
    good_offset: float = 1.0,
    seed: int = 0,
) -> Path:
    """Synthesise a captures.npz where good and derailed clusters are
    separated by `good_offset` along the FIRST hidden dim. Per-layer
    direction should therefore be ~[good_offset, 0, 0, ...]."""
    rng = np.random.default_rng(seed)
    n = n_good + n_derailed
    residuals = rng.normal(scale=0.01, size=(n, n_layers, hidden_dim)).astype(np.float32)
    residuals[:n_good, :, 0] += good_offset
    labels = np.array(["good"] * n_good + ["derailed"] * n_derailed, dtype=object)
    ids = np.array([f"p{i}" for i in range(n)], dtype=object)
    meta = json.dumps({"n_pairs": n, "n_layers": n_layers, "hidden_dim": hidden_dim})
    out = tmp_path / "captures.npz"
    np.savez_compressed(
        out, residuals=residuals, labels=labels, ids=ids,
        meta=np.array(meta, dtype=object),
    )
    return out


# --- _parse_layers --------------------------------------------------------


def test_parse_layers_single():
    assert _parse_layers("18", 24) == [18]


def test_parse_layers_csv():
    assert _parse_layers("16,17,18", 24) == [16, 17, 18]


def test_parse_layers_range():
    assert _parse_layers("16-20", 24) == [16, 17, 18, 19, 20]


def test_parse_layers_mixed():
    assert _parse_layers("0,2-4,10", 24) == [0, 2, 3, 4, 10]


def test_parse_layers_all():
    assert _parse_layers("all", 4) == [0, 1, 2, 3]


def test_parse_layers_dedup():
    assert _parse_layers("3,3,3-5,4", 10) == [3, 4, 5]


def test_parse_layers_out_of_range_raises():
    with pytest.raises(ValueError, match="out of range"):
        _parse_layers("99", 4)


def test_parse_layers_reversed_range_raises():
    with pytest.raises(ValueError, match="reversed"):
        _parse_layers("5-3", 10)


# --- compute_vectors -------------------------------------------------------


def test_compute_vectors_recovers_known_offset(tmp_path):
    npz = _make_captures_npz(tmp_path, good_offset=1.0)
    results = compute_vectors(npz, mode="show_work", layers="all")
    assert len(results) == 4
    for r in results:
        # Strongest component is dim 0, magnitude ~1.0.
        assert abs(r.vector[0] - 1.0) < 0.05, r.vector
        # Other components are noise — within 5σ of 0 given small samples.
        assert np.abs(r.vector[1:]).max() < 0.05
        assert r.n_good == 5
        assert r.n_derailed == 5
        assert r.normalized is False
        assert r.norm > 0.5


def test_compute_vectors_normalize(tmp_path):
    npz = _make_captures_npz(tmp_path, good_offset=2.5)
    results = compute_vectors(npz, mode="show_work", layers="0", normalize=True)
    assert len(results) == 1
    r = results[0]
    # Unit norm after normalization.
    assert abs(np.linalg.norm(r.vector) - 1.0) < 1e-5
    assert r.normalized is True
    # First-dim weight dominates after normalization.
    assert r.vector[0] > 0.95


def test_compute_vectors_specific_layers(tmp_path):
    npz = _make_captures_npz(tmp_path, n_layers=8)
    results = compute_vectors(npz, mode="m", layers="2,5")
    assert [r.layer for r in results] == [2, 5]


def test_compute_vectors_no_good_label_raises(tmp_path):
    npz = _make_captures_npz(tmp_path, n_good=0, n_derailed=5)
    with pytest.raises(ValueError, match="at least one"):
        compute_vectors(npz, mode="m", layers="0")


def test_compute_vectors_no_derailed_label_raises(tmp_path):
    npz = _make_captures_npz(tmp_path, n_good=5, n_derailed=0)
    with pytest.raises(ValueError, match="at least one"):
        compute_vectors(npz, mode="m", layers="0")


def test_compute_vectors_custom_labels(tmp_path):
    """Override good/derailed label names — useful for non-binary
    schemes once M5 lands."""
    npz_path = tmp_path / "custom.npz"
    rng = np.random.default_rng(0)
    n_layers, hidden_dim = 2, 4
    residuals = rng.normal(scale=0.01, size=(6, n_layers, hidden_dim)).astype(np.float32)
    residuals[:3, :, 0] += 1.0
    labels = np.array(["pos"] * 3 + ["neg"] * 3, dtype=object)
    np.savez_compressed(
        npz_path, residuals=residuals, labels=labels,
        ids=np.array([f"p{i}" for i in range(6)], dtype=object),
        meta=np.array("{}", dtype=object),
    )
    results = compute_vectors(
        npz_path, mode="m", layers="all",
        good_label="pos", derailed_label="neg",
    )
    assert len(results) == 2
    for r in results:
        assert abs(r.vector[0] - 1.0) < 0.05


# --- write_vectors round-trip ---------------------------------------------


def test_write_vectors_round_trips_through_vector_load(tmp_path):
    npz = _make_captures_npz(tmp_path)
    results = compute_vectors(npz, mode="show_work", layers="0,2", normalize=True)
    out_root = tmp_path / "vectors"
    written = write_vectors(
        results, out_root,
        target_model="gemma4-26b-a4b",
        scale=0.5,
        provenance="test-suite",
    )
    assert len(written) == 2
    for manifest_path in written:
        # Tomllib can read it.
        with manifest_path.open("rb") as f:
            doc = tomllib.load(f)
        manifest = VectorManifest.from_toml_dict(doc)
        assert manifest.target_model == "gemma4-26b-a4b"
        assert manifest.scale == 0.5
        assert manifest.hidden_dim == 8
        assert manifest.research_provenance == "test-suite"
        assert manifest.mode_tags == ("show_work",)
        assert manifest.family == "activation_diff_v1"
        # And Vector.load() integrity-checks the .npy bytes.
        v = Vector.load(manifest_path)
        assert v.manifest.layer in (0, 2)
        # Manifest sha256 == sha256(.npy bytes on disk).
        npy_path = manifest_path.with_suffix(".npy")
        import hashlib
        assert (
            v.manifest.sha256 == hashlib.sha256(npy_path.read_bytes()).hexdigest()
        )


def test_write_vectors_npy_loads_to_correct_shape(tmp_path):
    npz = _make_captures_npz(tmp_path)
    results = compute_vectors(npz, mode="show_work", layers="1")
    out_root = tmp_path / "vectors"
    written = write_vectors(
        results, out_root,
        target_model="gemma4-26b-a4b", scale=0.6, provenance="t",
    )
    npy_path = written[0].with_suffix(".npy")
    arr = np.load(npy_path)
    assert arr.shape == (8,)
    assert arr.dtype == np.float32


# --- M3 → M4 → M2 round-trip ----------------------------------------------


class _FakeBlock(nn.Module):
    def forward(self, hidden):
        return (hidden, None)


class _FakeInner(nn.Module):
    def __init__(self, n_layers):
        super().__init__()
        self.layers = nn.ModuleList([_FakeBlock() for _ in range(n_layers)])

    def forward(self, hidden):
        for layer in self.layers:
            hidden = layer(hidden)[0]
        return hidden


class _FakeCausalLM(nn.Module):
    def __init__(self, hidden_size=8, n_layers=4):
        super().__init__()
        self.config = type("Cfg", (), {"hidden_size": hidden_size})()
        self.model = _FakeInner(n_layers)

    def forward(self, hidden):
        return self.model(hidden)


def test_m3_to_m4_to_m2_round_trip(tmp_path):
    """The full Deep Noir loop, end to end:

    1. Build a synthetic captures.npz (M3 output shape).
    2. Run compute_vectors + write_vectors (M4) into a registry root.
    3. Load that registry (production code path).
    4. Construct a SteeringHookManager (M2) with a vector_lookup that
       pulls from the registry.
    5. Run a forward pass under activate(directive) and verify the
       residual stream actually shifted in the trained direction.

    This is the integration test the PRD's M5 acceptance ("output
    differs with vs. without header") will rely on once a real model
    is in the loop — here we run it on a fake model to keep the test
    GPU-free, but the wiring is identical.
    """
    # 1. Synthesise captures with a known direction at layer 1.
    npz = _make_captures_npz(
        tmp_path, n_good=10, n_derailed=10, n_layers=4, hidden_dim=8,
        good_offset=2.0, seed=42,
    )
    # 2. Train + write.
    results = compute_vectors(npz, mode="show_work", layers="1", normalize=True)
    registry_root = tmp_path / "registry"
    write_vectors(
        results, registry_root,
        target_model="gemma4-26b-a4b",
        scale=1.0,
        provenance="round-trip-test",
    )
    # 3. Load via the production registry path.
    registry = load_registry(registry_root)
    assert "show_work" in registry.list_modes()
    loaded = registry.load_for_mode("show_work")
    assert len(loaded) == 1
    loaded_v = loaded[0]
    assert loaded_v.manifest.layer == 1

    # 4. Build the hook manager with a registry-backed lookup
    #    (mirrors what server._build_vector_lookup does).
    import io

    def lookup(mode, layer):
        for v in registry.load_for_mode(mode):
            if v.manifest.layer == layer:
                arr = np.load(io.BytesIO(v.data))
                return torch.from_numpy(arr.astype(np.float32, copy=False))
        return None

    fake_model = _FakeCausalLM(hidden_size=8, n_layers=4)
    mgr = SteeringHookManager(fake_model, vector_lookup=lookup)
    try:
        # 5. Forward pass with steering active.
        h = torch.zeros(1, 1, 8)
        baseline = fake_model(h.clone())
        assert torch.allclose(baseline, torch.zeros(1, 1, 8))
        with mgr.activate([SteeringDirective(mode="show_work", layer=1, scale=1.0)]) as active:
            assert active is not None
            assert active.fired_layers() == [1]
            steered = fake_model(h.clone())
        # The trained vector should push hidden[..., 0] up; everything
        # else should stay near zero.
        assert steered[0, 0, 0].item() > 0.9, steered
        assert steered[..., 1:].abs().max().item() < 0.1
    finally:
        mgr.close()

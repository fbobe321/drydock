"""Tests for the Deep Noir steering scaffolding.

These exercise the vector/manifest format, registry discovery, the
config + applier seam, and the sandbox eval — all without needing a
real `.npy` payload (we generate raw bytes). The actual model-side
integration (vLLM forward-pass patch) is a Phase-3+ concern not in
this test surface.
"""
from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

import pytest

from drydock.steering import (
    NullSteeringApplier,
    SteeringConfig,
    apply_steering,
    load_registry,
)
from drydock.steering.applier import (
    LogOnlySteeringApplier,
    SteeringDecision,
)
from drydock.steering.sandbox import run_sandbox
from drydock.steering.vectors import (
    Vector,
    VectorIntegrityError,
    compute_sha256,
)


def _write_vector(
    root: Path,
    mode: str,
    name: str,
    *,
    layer: int = 12,
    scale: float = 0.5,
    target_model: str = "gemma4-26b-a4b",
    hidden_dim: int = 4096,
    payload: bytes | None = None,
    bad_sha: bool = False,
) -> Path:
    """Create a (toml, .npy) pair under root/mode/. Returns manifest path."""
    if payload is None:
        payload = b"\xab\xcd" * (hidden_dim * 2)
    mode_dir = root / mode
    mode_dir.mkdir(parents=True, exist_ok=True)
    payload_path = mode_dir / f"{name}.npy"
    payload_path.write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    if bad_sha:
        sha = "0" * 64
    manifest_path = mode_dir / f"{name}.toml"
    manifest_path.write_text(textwrap.dedent(f"""
        [vector]
        name = "{name}"
        description = "test vector"
        layer = {layer}
        scale = {scale}
        target_model = "{target_model}"
        hidden_dim = {hidden_dim}
        sha256 = "{sha}"

        [tags]
        mode = ["{mode}"]
        family = "test"
    """))
    return manifest_path


def test_vector_load_verifies_sha256(tmp_path: Path):
    mp = _write_vector(tmp_path, "secure_coding", "v1")
    v = Vector.load(mp)
    assert v.manifest.name == "v1"
    assert v.manifest.layer == 12
    assert v.manifest.scale == 0.5
    assert v.manifest.target_model == "gemma4-26b-a4b"
    assert v.manifest.mode_tags == ("secure_coding",)


def test_vector_load_rejects_sha_mismatch(tmp_path: Path):
    mp = _write_vector(tmp_path, "secure_coding", "v1", bad_sha=True)
    with pytest.raises(VectorIntegrityError):
        Vector.load(mp)


def test_compute_sha256_matches_load_path(tmp_path: Path):
    payload_path = tmp_path / "p.npy"
    payload_path.write_bytes(b"hello world")
    assert compute_sha256(payload_path) == hashlib.sha256(b"hello world").hexdigest()


def test_registry_discovers_modes(tmp_path: Path):
    _write_vector(tmp_path, "secure_coding", "v1")
    _write_vector(tmp_path, "secure_coding", "v2", layer=18)
    _write_vector(tmp_path, "anti_hallucination", "v1")
    reg = load_registry(tmp_path)
    assert set(reg.list_modes()) == {"secure_coding", "anti_hallucination"}
    sc = reg.vectors_for_mode("secure_coding")
    assert {m.name for m in sc} == {"v1", "v2"}


def test_registry_handles_missing_root(tmp_path: Path):
    """Empty deployment is a valid state."""
    reg = load_registry(tmp_path / "nope")
    assert reg.list_modes() == []


def test_registry_skips_invalid_manifest(tmp_path: Path):
    _write_vector(tmp_path, "secure_coding", "good")
    bad_dir = tmp_path / "secure_coding"
    (bad_dir / "broken.toml").write_text("not = valid = toml = at all !!!")
    reg = load_registry(tmp_path)
    names = {m.name for m in reg.vectors_for_mode("secure_coding")}
    assert names == {"good"}   # broken one skipped, valid one kept


def test_registry_load_for_mode_filters_corrupt(tmp_path: Path):
    _write_vector(tmp_path, "x", "ok")
    _write_vector(tmp_path, "x", "bad", bad_sha=True)
    reg = load_registry(tmp_path)
    loaded = reg.load_for_mode("x")
    assert {v.manifest.name for v in loaded} == {"ok"}


def test_apply_steering_disabled_config_noops(tmp_path: Path):
    _write_vector(tmp_path, "secure_coding", "v1")
    reg = load_registry(tmp_path)
    decision = apply_steering(
        SteeringConfig.disabled(),
        reg,
        NullSteeringApplier(),
        active_model="gemma4-26b-a4b",
    )
    assert decision.is_noop()
    assert "disabled" in " ".join(decision.skipped_reasons).lower()


def test_apply_steering_log_only_records_match(tmp_path: Path):
    _write_vector(tmp_path, "secure_coding", "v1")
    reg = load_registry(tmp_path)
    config = SteeringConfig.from_mode_names(["secure_coding"])
    decision = apply_steering(
        config, reg, LogOnlySteeringApplier(), active_model="gemma4-26b-a4b-it"
    )
    assert not decision.is_noop()
    assert decision.applied_vectors[0].manifest.name == "v1"
    assert decision.applier_kind == "log_only"


def test_apply_steering_skips_wrong_target_model(tmp_path: Path):
    _write_vector(tmp_path, "secure_coding", "v1", target_model="llama3-70b")
    reg = load_registry(tmp_path)
    config = SteeringConfig.from_mode_names(["secure_coding"])
    decision = apply_steering(
        config, reg, LogOnlySteeringApplier(), active_model="gemma4-26b-a4b"
    )
    # The vector loads but the applier rejects on target_model mismatch.
    assert decision.is_noop()


def test_apply_steering_scale_override(tmp_path: Path):
    _write_vector(tmp_path, "x", "v1", scale=0.3)
    reg = load_registry(tmp_path)
    config = SteeringConfig.from_mode_names(["x"], scales={"x": 0.9})
    decision = apply_steering(
        config, reg, LogOnlySteeringApplier(), active_model="gemma4-26b-a4b"
    )
    assert decision.applied_vectors[0].manifest.scale == 0.9


def test_apply_steering_unknown_mode_records_reason(tmp_path: Path):
    reg = load_registry(tmp_path)
    config = SteeringConfig.from_mode_names(["nonexistent"])
    decision = apply_steering(
        config, reg, LogOnlySteeringApplier(), active_model="gemma4-26b-a4b"
    )
    assert decision.is_noop()
    assert any("nonexistent" in r for r in decision.skipped_reasons)


def test_sandbox_runs_with_log_only_applier(tmp_path: Path):
    _write_vector(tmp_path, "x", "v1")
    reg = load_registry(tmp_path)
    config = SteeringConfig.from_mode_names(["x"])

    calls: list[tuple[str, int]] = []

    def fake_completion(prompt: str, decision: SteeringDecision) -> str:
        calls.append((prompt, len(decision.applied_vectors)))
        if decision.applied_vectors:
            return "STEERED: " + prompt
        return "BASELINE: " + prompt

    summary = run_sandbox(
        config=config,
        prompts=["hello", "world"],
        registry=reg,
        applier=LogOnlySteeringApplier(),
        completion_fn=fake_completion,
        active_model="gemma4-26b-a4b",
        bad_patterns=(),
    )
    # 2 prompts × 2 calls each (baseline + steered) = 4
    assert len(calls) == 4
    assert len(summary.per_prompt) == 2
    assert summary.distinct_outputs == 2
    assert summary.unchanged_outputs == 0
    assert summary.regressions == 0
    assert summary.passed()


def test_sandbox_detects_regression(tmp_path: Path):
    _write_vector(tmp_path, "x", "v1")
    reg = load_registry(tmp_path)
    config = SteeringConfig.from_mode_names(["x"])

    def fake_completion(prompt: str, decision: SteeringDecision) -> str:
        if decision.applied_vectors:
            return "import os; os.system('rm -rf /')"   # bad pattern appears
        return "import os"

    summary = run_sandbox(
        config=config,
        prompts=["write a python script"],
        registry=reg,
        applier=LogOnlySteeringApplier(),
        completion_fn=fake_completion,
        active_model="gemma4-26b-a4b",
        bad_patterns=("os.system",),
    )
    assert summary.regressions == 1
    assert not summary.passed()


def test_sandbox_detects_fix(tmp_path: Path):
    _write_vector(tmp_path, "x", "v1")
    reg = load_registry(tmp_path)
    config = SteeringConfig.from_mode_names(["x"])

    def fake_completion(prompt: str, decision: SteeringDecision) -> str:
        if decision.applied_vectors:
            return "subprocess.run(['ls'], shell=False)"   # safe
        return "os.system('ls')"                           # unsafe

    summary = run_sandbox(
        config=config,
        prompts=["list files"],
        registry=reg,
        applier=LogOnlySteeringApplier(),
        completion_fn=fake_completion,
        active_model="gemma4-26b-a4b",
        bad_patterns=("os.system",),
    )
    assert summary.fixes == 1
    assert summary.regressions == 0
    assert summary.passed()


def test_sandbox_writes_json(tmp_path: Path):
    _write_vector(tmp_path, "x", "v1")
    reg = load_registry(tmp_path)
    config = SteeringConfig.from_mode_names(["x"])

    summary = run_sandbox(
        config=config,
        prompts=["only one"],
        registry=reg,
        applier=LogOnlySteeringApplier(),
        completion_fn=lambda p, d: "done",
        active_model="gemma4-26b-a4b",
    )
    out_path = tmp_path / "results" / "summary.json"
    summary.write_json(out_path)
    assert out_path.is_file()
    import json
    data = json.loads(out_path.read_text())
    assert data["modes"] == ["x"]
    assert "passed" in data


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

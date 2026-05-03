"""Tests for the agent-loop steering hook.

Verifies:
- No-op when env unset (zero behavior change for existing deployments)
- Returns a summary line when env is set
- Caches across calls (registry not reloaded each turn)
- Never raises — even if the steering layer is broken
"""
from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

import pytest

from drydock.core import steering_hook


@pytest.fixture(autouse=True)
def _reset_cache():
    steering_hook.reset_cache_for_tests()
    yield
    steering_hook.reset_cache_for_tests()


def test_returns_none_when_env_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DRYDOCK_STEERING_MODES", raising=False)
    assert steering_hook.apply_for_request("gemma4") is None


def test_returns_none_when_env_blank(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DRYDOCK_STEERING_MODES", "")
    assert steering_hook.apply_for_request("gemma4") is None
    monkeypatch.setenv("DRYDOCK_STEERING_MODES", "  ,  ")
    steering_hook.reset_cache_for_tests()
    assert steering_hook.apply_for_request("gemma4") is None


def test_returns_summary_when_env_set_with_no_vectors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Modes set but no vectors on disk → still returns a summary saying noop."""
    monkeypatch.setenv("DRYDOCK_STEERING_MODES", "secure_coding")
    monkeypatch.setenv("DRYDOCK_STEERING_ROOT", str(tmp_path / "empty"))
    summary = steering_hook.apply_for_request("gemma4")
    assert summary is not None
    assert "noop" in summary or "secure_coding" in summary


def _write_vector(root: Path, mode: str, name: str, target_model: str = "gemma4"):
    payload = b"\xab\xcd" * 4096
    sha = hashlib.sha256(payload).hexdigest()
    mode_dir = root / mode
    mode_dir.mkdir(parents=True, exist_ok=True)
    (mode_dir / f"{name}.npy").write_bytes(payload)
    (mode_dir / f"{name}.toml").write_text(textwrap.dedent(f"""
        [vector]
        name = "{name}"
        description = "test"
        layer = 12
        scale = 0.5
        target_model = "{target_model}"
        hidden_dim = 4096
        sha256 = "{sha}"
        [tags]
        mode = ["{mode}"]
    """))


def test_returns_summary_when_vector_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _write_vector(tmp_path, "secure_coding", "v1")
    monkeypatch.setenv("DRYDOCK_STEERING_MODES", "secure_coding")
    monkeypatch.setenv("DRYDOCK_STEERING_ROOT", str(tmp_path))
    summary = steering_hook.apply_for_request("gemma4")
    assert summary is not None
    assert "v1" in summary or "log_only" in summary


def test_cache_is_reused_across_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _write_vector(tmp_path, "x", "v1")
    monkeypatch.setenv("DRYDOCK_STEERING_MODES", "x")
    monkeypatch.setenv("DRYDOCK_STEERING_ROOT", str(tmp_path))
    s1 = steering_hook.apply_for_request("gemma4")
    s2 = steering_hook.apply_for_request("gemma4")
    assert s1 == s2
    assert s1 is not None


def test_never_raises_on_corrupt_vector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A corrupt-sha256 vector must not break the agent loop — the
    registry skips it during load_for_mode, the apply call returns
    a noop summary."""
    # Manifest with bad sha256 — payload won't match
    mode_dir = tmp_path / "x"
    mode_dir.mkdir(parents=True)
    (mode_dir / "v1.npy").write_bytes(b"\x00" * 8)
    (mode_dir / "v1.toml").write_text(textwrap.dedent("""
        [vector]
        name = "v1"
        description = "broken"
        layer = 12
        scale = 0.5
        target_model = "gemma4"
        hidden_dim = 4096
        sha256 = "0000000000000000000000000000000000000000000000000000000000000000"
        [tags]
        mode = ["x"]
    """))
    monkeypatch.setenv("DRYDOCK_STEERING_MODES", "x")
    monkeypatch.setenv("DRYDOCK_STEERING_ROOT", str(tmp_path))
    summary = steering_hook.apply_for_request("gemma4")
    # Returns a summary (not None) because env is set, but no vectors applied
    assert summary is not None
    assert "noop" in summary


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

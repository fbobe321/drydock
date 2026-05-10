"""Tests for M3 capture-mode hooks + the train.capture CLI.

Two surfaces:

- `CaptureHookManager` against a tiny torch.nn module — verifies that
  the read-only path records the right shape per layer at the right
  token position, that out-of-range positions / batch indices fail
  loud-but-soft, that close() stops capture.
- `drydock.steering.train.capture.main` end-to-end — monkeypatches
  the loader to inject a fake model + tokenizer, runs over a synthetic
  pairs.jsonl, asserts the .npz has the right shape and content.
"""
from __future__ import annotations

import json

import pytest

torch = pytest.importorskip("torch")
np = pytest.importorskip("numpy")
import torch.nn as nn  # noqa: E402

from drydock.steering.sidecar.hooks import (  # noqa: E402
    CaptureHookManager,
    SteeringHookManager,
    get_active_capture,
)


# --- shared fakes ----------------------------------------------------------


class _FakeBlock(nn.Module):
    def forward(self, hidden):
        # Return tuple (hidden, present_kv) like transformers blocks do.
        return (hidden, None)


class _FakeInner(nn.Module):
    def __init__(self, n_layers: int):
        super().__init__()
        self.layers = nn.ModuleList([_FakeBlock() for _ in range(n_layers)])

    def forward(self, hidden):
        for layer in self.layers:
            hidden = layer(hidden)[0]
        return hidden


class _FakeCausalLM(nn.Module):
    def __init__(self, hidden_size: int = 8, n_layers: int = 4):
        super().__init__()
        self.config = type("Cfg", (), {"hidden_size": hidden_size})()
        self.model = _FakeInner(n_layers)
        self.device = torch.device("cpu")

    def forward(self, input_ids):
        # Treat input_ids as if it were already an embedding for testing.
        # The capture path only needs forward to flow through layers; it
        # doesn't actually care about tokens.
        if input_ids.dtype in (torch.long, torch.int):
            # Convert (batch, seq) → (batch, seq, hidden) one-hot-ish
            batch, seq = input_ids.shape
            hidden = torch.zeros(
                batch, seq, self.config.hidden_size, dtype=torch.float32
            )
            for b in range(batch):
                for s in range(seq):
                    hidden[b, s, int(input_ids[b, s]) % self.config.hidden_size] = 1.0
        else:
            hidden = input_ids
        return self.model(hidden)


# --- CaptureHookManager ----------------------------------------------------


def test_inactive_capture_is_passthrough():
    m = _FakeCausalLM(hidden_size=4, n_layers=3)
    mgr = CaptureHookManager(m)
    h = torch.randn(1, 5, 4)
    out = m(h.clone())
    # No capture buffer set → buf empty, output unchanged
    assert torch.allclose(out, h)
    assert get_active_capture() is None
    mgr.close()


def test_capture_records_one_residual_per_layer():
    m = _FakeCausalLM(hidden_size=4, n_layers=3)
    mgr = CaptureHookManager(m)
    h = torch.randn(1, 5, 4)
    with mgr.capture(position=-1) as buf:
        m(h.clone())
    assert set(buf.residuals.keys()) == {0, 1, 2}
    for idx, vec in buf.residuals.items():
        assert vec.shape == (4,)
        assert vec.dtype == torch.float32


def test_capture_position_picks_correct_token():
    """At position=2 in a 5-token sequence, the captured residual must
    equal hidden[batch=0, token=2, :] for each layer (the fake model is
    a no-op pass through, so layer output == input)."""
    m = _FakeCausalLM(hidden_size=4, n_layers=2)
    mgr = CaptureHookManager(m)
    h = torch.arange(20, dtype=torch.float32).reshape(1, 5, 4)
    with mgr.capture(position=2) as buf:
        m(h.clone())
    expected = h[0, 2]   # tensor([8., 9., 10., 11.])
    for layer_idx, vec in buf.residuals.items():
        assert torch.allclose(vec, expected), f"layer {layer_idx} mismatch"


def test_capture_negative_position_indexes_from_end():
    m = _FakeCausalLM(hidden_size=4, n_layers=1)
    mgr = CaptureHookManager(m)
    h = torch.arange(20, dtype=torch.float32).reshape(1, 5, 4)
    with mgr.capture(position=-1) as buf:
        m(h.clone())
    # position=-1 → last token (index 4)
    assert torch.allclose(buf.residuals[0], h[0, 4])


def test_capture_out_of_range_position_skips_silently():
    m = _FakeCausalLM(hidden_size=4, n_layers=2)
    mgr = CaptureHookManager(m)
    h = torch.randn(1, 3, 4)
    with mgr.capture(position=99) as buf:
        m(h.clone())
    # Hooks log a warning and skip — buffer ends empty.
    assert buf.residuals == {}


def test_capture_buffer_stack_pads_missing_layers_with_zeros():
    m = _FakeCausalLM(hidden_size=4, n_layers=3)
    mgr = CaptureHookManager(m)
    h = torch.ones(1, 2, 4)
    with mgr.capture(position=0) as buf:
        m(h.clone())
    stacked = buf.stack(mgr.n_layers)
    assert stacked.shape == (3, 4)


def test_capture_stack_raises_when_empty():
    m = _FakeCausalLM(hidden_size=4, n_layers=2)
    mgr = CaptureHookManager(m)
    from drydock.steering.sidecar.hooks import CaptureBuffer
    empty = CaptureBuffer()
    with pytest.raises(RuntimeError, match="empty"):
        empty.stack(mgr.n_layers)


def test_capture_close_stops_recording():
    m = _FakeCausalLM(hidden_size=4, n_layers=2)
    mgr = CaptureHookManager(m)
    mgr.close()
    h = torch.ones(1, 2, 4)
    with mgr.capture(position=0) as buf:
        m(h.clone())
    # Hooks removed → nothing captured.
    assert buf.residuals == {}


def test_capture_and_steering_hooks_coexist():
    """Both managers register independent hooks on the same model."""
    m = _FakeCausalLM(hidden_size=4, n_layers=2)
    cap = CaptureHookManager(m)
    steer = SteeringHookManager(m, vector_lookup=lambda mode, layer: torch.zeros(4))
    h = torch.randn(1, 2, 4)
    with cap.capture(position=-1) as buf:
        m(h.clone())
    assert set(buf.residuals.keys()) == {0, 1}
    cap.close()
    steer.close()


# --- train.capture CLI -----------------------------------------------------


class _FakeTokenizer:
    """Minimal tokenizer mock: byte-encodes text into a (1, N) LongTensor."""

    def __call__(self, text, return_tensors=None, add_special_tokens=True):
        ids = torch.tensor(
            [[(ord(c) % 16) for c in text] or [0]], dtype=torch.long
        )
        return {"input_ids": ids}


@pytest.fixture
def synth_pairs(tmp_path):
    path = tmp_path / "pairs.jsonl"
    records = [
        {"prompt": "hello ", "completion": "world", "label": "good", "id": "p1"},
        {"prompt": "foo ", "completion": "bar baz", "label": "derailed", "id": "p2"},
        {"prompt": "x", "completion": "y", "label": "good"},  # no id → auto
    ]
    path.write_text("\n".join(json.dumps(r) for r in records))
    return path


def test_capture_cli_end_to_end(monkeypatch, tmp_path, synth_pairs):
    fake_model = _FakeCausalLM(hidden_size=4, n_layers=3)
    fake_tokenizer = _FakeTokenizer()

    def fake_load_model():
        return fake_model, fake_tokenizer

    monkeypatch.setattr(
        "drydock.steering.sidecar.loader.load_model", fake_load_model
    )
    # capture.py imports load_model inside main(); monkeypatch via the
    # canonical module attribute is enough.

    from drydock.steering.train import capture as capture_mod

    out_path = tmp_path / "captures.npz"
    rc = capture_mod.main([
        "--pairs", str(synth_pairs),
        "--out", str(out_path),
        "--position", "last",
        "--log-level", "WARNING",
    ])
    assert rc == 0
    assert out_path.exists()

    data = np.load(out_path, allow_pickle=True)
    assert data["residuals"].shape == (3, 3, 4)   # (n_pairs, n_layers, hidden_dim)
    assert data["residuals"].dtype == np.float32
    labels = list(data["labels"])
    assert labels == ["good", "derailed", "good"]
    ids = list(data["ids"])
    assert ids[0] == "p1"
    assert ids[1] == "p2"
    # Auto-generated id for the third record uses pairs.jsonl stem
    assert "pairs" in ids[2]
    meta = json.loads(str(data["meta"]))
    assert meta["n_pairs"] == 3
    assert meta["n_layers"] == 3
    assert meta["hidden_dim"] == 4
    assert meta["position_mode"] == "last"
    assert meta["schema_version"] == 1


def test_capture_cli_invalid_pair_exits(tmp_path):
    from drydock.steering.train import capture as capture_mod
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not-json\n")
    out_path = tmp_path / "out.npz"
    with pytest.raises(SystemExit) as exc:
        capture_mod.main([
            "--pairs", str(bad),
            "--out", str(out_path),
            "--log-level", "ERROR",
        ])
    assert "invalid JSON" in str(exc.value)


def test_capture_cli_missing_required_field_exits(tmp_path):
    from drydock.steering.train import capture as capture_mod
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"prompt": "x"}) + "\n")  # no completion
    out_path = tmp_path / "out.npz"
    with pytest.raises(SystemExit) as exc:
        capture_mod.main([
            "--pairs", str(bad),
            "--out", str(out_path),
            "--log-level", "ERROR",
        ])
    assert "required" in str(exc.value)


def test_capture_cli_max_pairs(monkeypatch, tmp_path, synth_pairs):
    fake_model = _FakeCausalLM(hidden_size=4, n_layers=2)
    fake_tokenizer = _FakeTokenizer()
    monkeypatch.setattr(
        "drydock.steering.sidecar.loader.load_model",
        lambda: (fake_model, fake_tokenizer),
    )
    from drydock.steering.train import capture as capture_mod
    out_path = tmp_path / "small.npz"
    rc = capture_mod.main([
        "--pairs", str(synth_pairs),
        "--out", str(out_path),
        "--max-pairs", "1",
        "--log-level", "ERROR",
    ])
    assert rc == 0
    data = np.load(out_path, allow_pickle=True)
    assert data["residuals"].shape == (1, 2, 4)
    assert list(data["labels"]) == ["good"]

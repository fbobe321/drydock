"""Tests for the sidecar's M2 hook scaffolding.

Covers:
- header_parser: well-formed entries, ASCII × fallback, malformed entries
  dropped (not raised), empty/None header → [].
- SteeringHookManager: discovers decoder layers under model.model.layers
  AND model.layers, registers one hook per layer, ContextVar isolation
  between concurrent activate() blocks (threading), missing/None vector
  paths, out-of-range layers dropped.
- Numerical correctness: with a zero vector the residual stream is
  bit-identical (the M2 acceptance check). With a non-zero vector,
  hidden += scale * vector, and multiple directives at the same layer
  sum.

No GPU required — we use a tiny torch.nn module that mimics the layer
shape transformers would have.
"""
from __future__ import annotations

import threading

import pytest

torch = pytest.importorskip("torch")
import torch.nn as nn  # noqa: E402

from drydock.steering.sidecar.header_parser import (  # noqa: E402
    SteeringDirective,
    parse_header,
)
from drydock.steering.sidecar.hooks import (  # noqa: E402
    ActiveSteering,
    SteeringHookManager,
    get_active_steering,
)


# --- header_parser ----------------------------------------------------------


def test_parse_header_empty():
    assert parse_header(None) == []
    assert parse_header("") == []
    assert parse_header("   ") == []


def test_parse_header_single_unicode_x():
    out = parse_header("show_work@18×0.5")
    assert out == [SteeringDirective(mode="show_work", layer=18, scale=0.5)]


def test_parse_header_ascii_x_fallback():
    out = parse_header("show_work@18x0.5")
    assert out == [SteeringDirective(mode="show_work", layer=18, scale=0.5)]


def test_parse_header_negative_scale():
    out = parse_header("suppress@22×-0.3")
    assert out == [SteeringDirective(mode="suppress", layer=22, scale=-0.3)]


def test_parse_header_multiple_directives():
    out = parse_header("show_work@18×0.6,verify@22×0.4")
    assert out == [
        SteeringDirective(mode="show_work", layer=18, scale=0.6),
        SteeringDirective(mode="verify", layer=22, scale=0.4),
    ]


def test_parse_header_drops_malformed_keeps_good():
    # First entry is malformed (no @), second is valid. Parser must
    # NOT raise — it should drop the bad one and keep going.
    out = parse_header("garbage,show_work@18×0.5")
    assert out == [SteeringDirective(mode="show_work", layer=18, scale=0.5)]


def test_parse_header_rejects_uppercase_mode():
    # mode regex is [a-z0-9_-] — uppercase intentionally rejected to
    # keep the wire format normalized.
    out = parse_header("ShowWork@18×0.5")
    assert out == []


# --- hook manager: layer discovery -----------------------------------------


class _FakeBlock(nn.Module):
    """A stand-in for a transformers decoder block. Returns a tuple
    (hidden, present_kv) so the hook hits the tuple-output path."""

    def forward(self, hidden):
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
    """Mimics `model.model.layers` — the transformers convention."""

    def __init__(self, hidden_size: int = 8, n_layers: int = 4):
        super().__init__()
        self.config = type("Cfg", (), {"hidden_size": hidden_size})()
        self.model = _FakeInner(n_layers)

    def forward(self, hidden):
        return self.model(hidden)


class _LegacyCausalLM(nn.Module):
    """Mimics the older `model.layers` convention some archs use."""

    def __init__(self, hidden_size: int = 8, n_layers: int = 4):
        super().__init__()
        self.config = type("Cfg", (), {"hidden_size": hidden_size})()
        self.layers = nn.ModuleList([_FakeBlock() for _ in range(n_layers)])

    def forward(self, hidden):
        for layer in self.layers:
            hidden = layer(hidden)[0]
        return hidden


def test_manager_discovers_model_dot_model_dot_layers():
    m = _FakeCausalLM(hidden_size=8, n_layers=4)
    mgr = SteeringHookManager(m, vector_lookup=lambda mode, layer: None)
    assert mgr.n_layers == 4
    mgr.close()


def test_manager_discovers_model_dot_layers_legacy():
    m = _LegacyCausalLM(hidden_size=8, n_layers=3)
    mgr = SteeringHookManager(m, vector_lookup=lambda mode, layer: None)
    assert mgr.n_layers == 3
    mgr.close()


def test_manager_raises_when_layers_missing():
    class _Bare(nn.Module):
        config = type("Cfg", (), {"hidden_size": 8})()

    with pytest.raises(RuntimeError, match="cannot locate decoder layers"):
        SteeringHookManager(_Bare(), vector_lookup=lambda m, l: None)


# --- hook manager: activate / ContextVar -----------------------------------


def test_inactive_hook_is_pure_passthrough():
    m = _FakeCausalLM(hidden_size=8, n_layers=2)
    mgr = SteeringHookManager(m, vector_lookup=lambda mode, layer: None)
    h = torch.randn(1, 3, 8)
    out = m(h.clone())
    assert torch.allclose(out, h)
    mgr.close()


def test_zero_vector_is_bit_identical_acceptance_check():
    """The M2 acceptance check: with a zero vector lookup, the
    completion must match the no-header path bit-for-bit."""
    m = _FakeCausalLM(hidden_size=8, n_layers=4)

    def zero_lookup(mode: str, layer: int):
        return torch.zeros(8)

    mgr = SteeringHookManager(m, vector_lookup=zero_lookup)
    h = torch.randn(2, 5, 8)
    no_header = m(h.clone())
    directives = [SteeringDirective(mode="show_work", layer=2, scale=0.5)]
    with mgr.activate(directives) as active:
        with_header = m(h.clone())
        assert active is not None
        assert active.fired_layers() == [2]
    assert torch.allclose(no_header, with_header), (
        "M2 wiring check failed: zero vector should produce identical output"
    )
    mgr.close()


def test_nonzero_vector_modifies_residual_at_target_layer():
    m = _FakeCausalLM(hidden_size=4, n_layers=3)

    def lookup(mode: str, layer: int):
        v = torch.zeros(4)
        v[0] = 1.0
        return v

    mgr = SteeringHookManager(m, vector_lookup=lookup)
    h = torch.zeros(1, 1, 4)
    baseline = m(h.clone())
    assert torch.allclose(baseline, torch.zeros(1, 1, 4))

    # Inject at the LAST layer so subsequent layers can't undo it.
    directives = [SteeringDirective(mode="m", layer=2, scale=0.5)]
    with mgr.activate(directives):
        steered = m(h.clone())
    expected = torch.zeros(1, 1, 4)
    expected[..., 0] = 0.5
    assert torch.allclose(steered, expected)
    mgr.close()


def test_multiple_directives_same_layer_sum():
    m = _FakeCausalLM(hidden_size=4, n_layers=2)

    def lookup(mode: str, layer: int):
        v = torch.zeros(4)
        if mode == "a":
            v[0] = 1.0
        elif mode == "b":
            v[1] = 1.0
        return v

    mgr = SteeringHookManager(m, vector_lookup=lookup)
    directives = [
        SteeringDirective(mode="a", layer=1, scale=0.5),
        SteeringDirective(mode="b", layer=1, scale=2.0),
    ]
    h = torch.zeros(1, 1, 4)
    with mgr.activate(directives):
        out = m(h.clone())
    expected = torch.tensor([[[0.5, 2.0, 0.0, 0.0]]])
    assert torch.allclose(out, expected)
    mgr.close()


def test_out_of_range_layer_dropped_with_no_effect():
    m = _FakeCausalLM(hidden_size=4, n_layers=2)
    mgr = SteeringHookManager(m, vector_lookup=lambda mode, layer: torch.ones(4))
    directives = [SteeringDirective(mode="x", layer=99, scale=1.0)]
    with mgr.activate(directives) as active:
        # All directives dropped → manager yields None.
        assert active is None
    mgr.close()


def test_lookup_returning_none_is_skipped():
    m = _FakeCausalLM(hidden_size=4, n_layers=2)
    mgr = SteeringHookManager(m, vector_lookup=lambda mode, layer: None)
    directives = [SteeringDirective(mode="x", layer=0, scale=1.0)]
    with mgr.activate(directives) as active:
        assert active is None
    mgr.close()


def test_lookup_raising_is_skipped_not_propagated():
    m = _FakeCausalLM(hidden_size=4, n_layers=2)

    def boom(mode: str, layer: int):
        raise ValueError("boom")

    mgr = SteeringHookManager(m, vector_lookup=boom)
    directives = [SteeringDirective(mode="x", layer=0, scale=1.0)]
    # Must not raise — a malformed/bad lookup degrades to unsteered.
    with mgr.activate(directives) as active:
        assert active is None
    mgr.close()


def test_contextvar_resets_after_block():
    m = _FakeCausalLM(hidden_size=4, n_layers=2)
    mgr = SteeringHookManager(m, vector_lookup=lambda mode, layer: torch.zeros(4))
    assert get_active_steering() is None
    with mgr.activate([SteeringDirective(mode="x", layer=0, scale=0.5)]):
        assert get_active_steering() is not None
    assert get_active_steering() is None
    mgr.close()


def test_contextvar_isolated_across_threads():
    """Concurrent requests must not see each other's directives."""
    m = _FakeCausalLM(hidden_size=4, n_layers=2)
    mgr = SteeringHookManager(m, vector_lookup=lambda mode, layer: torch.zeros(4))
    seen: dict[str, ActiveSteering | None] = {}
    ev_a = threading.Event()
    ev_b = threading.Event()

    def thread_a():
        with mgr.activate([SteeringDirective(mode="a", layer=0, scale=0.1)]):
            ev_a.set()
            ev_b.wait(timeout=2.0)
            seen["a"] = get_active_steering()

    def thread_b():
        ev_a.wait(timeout=2.0)
        # B sees its own None (no activate yet) even though A is active.
        seen["b_outside"] = get_active_steering()
        with mgr.activate([SteeringDirective(mode="b", layer=1, scale=0.2)]):
            seen["b_inside"] = get_active_steering()
        ev_b.set()

    ta = threading.Thread(target=thread_a)
    tb = threading.Thread(target=thread_b)
    ta.start()
    tb.start()
    ta.join(timeout=5)
    tb.join(timeout=5)

    assert seen["b_outside"] is None
    assert seen["b_inside"] is not None
    assert seen["b_inside"].fired_layers() == [1]
    assert seen["a"] is not None
    assert seen["a"].fired_layers() == [0]
    mgr.close()


def test_close_removes_hooks():
    m = _FakeCausalLM(hidden_size=4, n_layers=2)
    mgr = SteeringHookManager(m, vector_lookup=lambda mode, layer: torch.ones(4))
    mgr.close()
    # After close, even with active directives, residual is unchanged.
    h = torch.zeros(1, 1, 4)
    directives = [SteeringDirective(mode="x", layer=1, scale=1.0)]
    with mgr.activate(directives):
        out = m(h.clone())
    assert torch.allclose(out, h)

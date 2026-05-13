"""Unit tests for `prepare_chat_inputs` — the chat-template conversion
that broke under transformers 5.x because `apply_chat_template` returns
a BatchEncoding by default (whereas older code expected a bare Tensor).

These tests cover the three shapes the helper must accept:

1. Modern transformers: `return_dict=True` honoured → BatchEncoding /
   dict-like with `input_ids` + `attention_mask`.
2. Older transformers: `return_dict` raises TypeError → helper falls
   back to tokenize=False then `tokenizer(text, return_tensors='pt')`.
3. Edge: some tokenizers ignore `return_dict` and still hand back a
   bare Tensor → helper wraps it as `{'input_ids': tensor}`.

We use a hand-rolled fake tokenizer rather than HF-hub-downloading
because the test must run offline and not pay model-fetch cost on every
pytest invocation.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def torch_mod():
    """Skip the whole file if torch isn't installed (CI minimal envs)."""
    torch = pytest.importorskip("torch")
    return torch


def _seq_to_tensor(torch, ids):
    return torch.tensor([ids], dtype=torch.long)


class _FakeModernTokenizer:
    """Mimics transformers >=5 — return_dict=True returns dict with
    input_ids + attention_mask. Without return_dict, returns Tensor."""

    def __init__(self, torch):
        self._torch = torch

    def apply_chat_template(
        self, messages, *, add_generation_prompt=False,
        return_tensors=None, return_dict=False, tokenize=True,
    ):
        if not tokenize:
            return "USER: " + messages[-1]["content"]
        ids = [10, 11, 12, 13]
        if return_dict:
            return {
                "input_ids": _seq_to_tensor(self._torch, ids),
                "attention_mask": _seq_to_tensor(self._torch, [1] * len(ids)),
            }
        return _seq_to_tensor(self._torch, ids)

    def __call__(self, text, *, return_tensors=None):
        # Used by the fallback path.
        return {
            "input_ids": _seq_to_tensor(self._torch, [20, 21, 22]),
            "attention_mask": _seq_to_tensor(self._torch, [1, 1, 1]),
        }


class _FakeLegacyTokenizer:
    """Mimics older transformers that don't accept return_dict."""

    def __init__(self, torch):
        self._torch = torch

    def apply_chat_template(
        self, messages, *, add_generation_prompt=False,
        return_tensors=None, tokenize=True,
    ):
        # Note: no return_dict kwarg. Passing return_dict trips TypeError.
        if not tokenize:
            return "USER: " + messages[-1]["content"]
        return _seq_to_tensor(self._torch, [30, 31, 32])

    def __call__(self, text, *, return_tensors=None):
        return {"input_ids": _seq_to_tensor(self._torch, [40, 41, 42, 43])}


class _FakeTensorOnlyTokenizer:
    """Mimics tokenizers that ignore return_dict and still hand back a Tensor."""

    def __init__(self, torch):
        self._torch = torch

    def apply_chat_template(
        self, messages, *, add_generation_prompt=False,
        return_tensors=None, return_dict=False, tokenize=True,
    ):
        return _seq_to_tensor(self._torch, [50, 51])


def test_modern_tokenizer_returns_dict_with_attention_mask(torch_mod):
    from drydock.steering.sidecar.server import prepare_chat_inputs

    tok = _FakeModernTokenizer(torch_mod)
    out = prepare_chat_inputs(tok, [{"role": "user", "content": "hi"}])
    assert "input_ids" in out
    assert "attention_mask" in out
    assert out["input_ids"].shape == (1, 4)
    assert out["attention_mask"].shape == (1, 4)


def test_modern_tokenizer_moves_to_device(torch_mod):
    from drydock.steering.sidecar.server import prepare_chat_inputs

    tok = _FakeModernTokenizer(torch_mod)
    out = prepare_chat_inputs(
        tok, [{"role": "user", "content": "hi"}], device=torch_mod.device("cpu")
    )
    assert out["input_ids"].device.type == "cpu"
    assert out["attention_mask"].device.type == "cpu"


def test_legacy_tokenizer_falls_back_to_two_step(torch_mod):
    from drydock.steering.sidecar.server import prepare_chat_inputs

    tok = _FakeLegacyTokenizer(torch_mod)
    out = prepare_chat_inputs(tok, [{"role": "user", "content": "hi"}])
    # Fallback path uses tokenizer(text) which returns [40,41,42,43]
    assert out["input_ids"].tolist() == [[40, 41, 42, 43]]


def test_tensor_only_tokenizer_is_wrapped(torch_mod):
    from drydock.steering.sidecar.server import prepare_chat_inputs

    tok = _FakeTensorOnlyTokenizer(torch_mod)
    out = prepare_chat_inputs(tok, [{"role": "user", "content": "hi"}])
    assert "input_ids" in out
    assert out["input_ids"].tolist() == [[50, 51]]


def test_helper_raises_when_no_input_ids(torch_mod):
    """A misconfigured tokenizer that returns a dict without input_ids
    must surface a clear error, not a downstream KeyError."""
    from drydock.steering.sidecar.server import prepare_chat_inputs

    class _Bad:
        def apply_chat_template(self, messages, **kw):
            if kw.get("return_dict"):
                return {"pixel_values": _seq_to_tensor(torch_mod, [0])}
            return _seq_to_tensor(torch_mod, [0])

    with pytest.raises(RuntimeError, match="no input_ids"):
        prepare_chat_inputs(_Bad(), [{"role": "user", "content": "hi"}])

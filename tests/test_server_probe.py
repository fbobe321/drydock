"""Server context-window probe — the definitive 'what's capping me' diagnostic."""
from __future__ import annotations

import json

from drydock import providers


class _Resp:
    def __init__(self, body): self._b = json.dumps(body).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_probe_llamacpp_props_n_ctx(monkeypatch):
    import urllib.request
    def fake(url, timeout=0):
        assert url.endswith("/props")      # llama.cpp endpoint, /v1 stripped
        return _Resp({"default_generation_settings": {"n_ctx": 32768}})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert providers.probe_server_context("http://localhost:8000/v1") == 32768


def test_probe_vllm_max_model_len(monkeypatch):
    import urllib.request
    def fake(url, timeout=0):
        if url.endswith("/props"):
            raise OSError("no props on vLLM")
        return _Resp({"data": [{"id": "m", "max_model_len": 65536}]})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert providers.probe_server_context("http://localhost:8000/v1") == 65536


def test_probe_unreachable_is_none(monkeypatch):
    import urllib.request
    def boom(url, timeout=0): raise OSError("offline")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert providers.probe_server_context("http://localhost:9999/v1") is None


def test_probe_garbage_and_missing_fields(monkeypatch):
    import urllib.request
    def fake(url, timeout=0):
        return _Resp({"unexpected": "shape"})              # no n_ctx / max_model_len
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert providers.probe_server_context("http://localhost:8000/v1") is None


def test_probe_zero_or_negative_ignored(monkeypatch):
    import urllib.request
    def fake(url, timeout=0):
        if url.endswith("/props"):
            return _Resp({"default_generation_settings": {"n_ctx": 0}})
        return _Resp({"data": [{"max_model_len": -1}]})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert providers.probe_server_context("http://localhost:8000/v1") is None

"""Tests for the auto-detection of a running local LLM server.

These avoid actually opening sockets — `urllib.request.urlopen` is
monkey-patched so we exercise the parsing/error paths deterministically.
"""
from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from typing import Any
from unittest.mock import patch

import pytest

from drydock.core.config.local_detect import (
    LocalServerInfo,
    detect_local_llm,
    patch_config_for_local,
)


class _FakeResp:
    def __init__(self, payload: dict[str, Any], status: int = 200):
        self._buf = BytesIO(json.dumps(payload).encode("utf-8"))
        self.status = status

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._buf.read()


def _success_payload(model_id: str = "test-model") -> dict[str, Any]:
    return {"object": "list", "data": [{"id": model_id, "object": "model"}]}


def test_detect_returns_first_responsive_endpoint():
    """First endpoint that returns a model wins; later endpoints not probed."""
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):  # type: ignore[no-untyped-def]
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        calls.append(url)
        # llama.cpp (8080) responds; everything else would error if reached.
        if "8080" in url:
            return _FakeResp(_success_payload("devstral-q4"))
        raise urllib.error.URLError("connection refused")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        info = detect_local_llm()

    assert info is not None
    assert info.label == "llama.cpp"
    assert info.api_base == "http://127.0.0.1:8080/v1"
    assert info.model_name == "devstral-q4"
    # We hit the first endpoint and stopped (didn't probe others).
    assert calls == ["http://127.0.0.1:8080/v1/models"]


def test_detect_falls_through_to_later_endpoint():
    """First endpoint refused, second endpoint succeeds."""
    def fake_urlopen(req, timeout=None):  # type: ignore[no-untyped-def]
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        if "11434" in url:
            return _FakeResp(_success_payload("llama3:latest"))
        raise urllib.error.URLError("connection refused")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        info = detect_local_llm()

    assert info is not None
    assert info.label == "Ollama"
    assert info.model_name == "llama3:latest"


def test_detect_returns_none_when_all_endpoints_fail():
    def fake_urlopen(_req, timeout=None):  # type: ignore[no-untyped-def]
        raise urllib.error.URLError("connection refused")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert detect_local_llm() is None


def test_detect_skips_non_openai_compatible_response():
    """A 200 response without `data: [...]` (e.g., a non-LLM HTTP server
    that happens to listen on 8080) must not be mistaken for a match."""
    def fake_urlopen(_req, timeout=None):  # type: ignore[no-untyped-def]
        return _FakeResp({"hello": "world"})  # not OpenAI-compatible

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert detect_local_llm() is None


def test_detect_skips_empty_model_list():
    def fake_urlopen(_req, timeout=None):  # type: ignore[no-untyped-def]
        return _FakeResp({"object": "list", "data": []})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert detect_local_llm() is None


def test_detect_skips_malformed_first_model():
    def fake_urlopen(_req, timeout=None):  # type: ignore[no-untyped-def]
        return _FakeResp({"data": [{"object": "model"}]})  # no `id`

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert detect_local_llm() is None


def test_detect_handles_oserror():
    """Some socket failures raise OSError, not URLError."""
    def fake_urlopen(_req, timeout=None):  # type: ignore[no-untyped-def]
        raise OSError("Network is unreachable")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert detect_local_llm() is None


def test_patch_sets_active_and_provider_and_model():
    cfg = {
        "active_model": "devstral-2",
        "providers": [
            {"name": "mistral", "api_base": "https://api.mistral.ai/v1"},
            {"name": "llamacpp", "api_base": "http://127.0.0.1:8080/v1"},
        ],
        "models": [
            {"alias": "devstral-2", "name": "drydock-cli-latest", "provider": "mistral"},
            {"alias": "local", "name": "devstral", "provider": "llamacpp"},
        ],
    }
    info = LocalServerInfo(
        label="vLLM",
        api_base="http://127.0.0.1:8000/v1",
        model_name="gemma4",
    )
    patch_config_for_local(cfg, info)
    assert cfg["active_model"] == "local"
    # llamacpp provider repointed at the detected URL
    llamacpp = next(p for p in cfg["providers"] if p["name"] == "llamacpp")
    assert llamacpp["api_base"] == "http://127.0.0.1:8000/v1"
    # Mistral provider untouched
    mistral = next(p for p in cfg["providers"] if p["name"] == "mistral")
    assert mistral["api_base"] == "https://api.mistral.ai/v1"
    # local model name updated to the detected model id
    local_model = next(m for m in cfg["models"] if m["alias"] == "local")
    assert local_model["name"] == "gemma4"


def test_patch_handles_missing_keys_gracefully():
    """Don't crash if the default config shape is unexpected."""
    cfg: dict[str, Any] = {}
    info = LocalServerInfo(
        label="Ollama",
        api_base="http://127.0.0.1:11434/v1",
        model_name="llama3",
    )
    patch_config_for_local(cfg, info)
    # active_model still gets set even if providers/models are absent
    assert cfg["active_model"] == "local"


def test_patch_bakes_llama_cpp_anti_loop_recipe():
    """When the detected backend is llama.cpp, the local model should
    inherit the article-recommended Gemma 4 anti-loop recipe — temp 1.0
    and the extra_params block. (issue #15)"""
    cfg = {
        "providers": [
            {"name": "llamacpp", "api_base": "http://127.0.0.1:8080/v1"},
        ],
        "models": [
            {"alias": "local", "name": "placeholder", "provider": "llamacpp"},
        ],
    }
    info = LocalServerInfo(
        label="llama.cpp",
        api_base="http://192.168.50.21:8000/v1",
        model_name="gemma-4-26B-A4B-it-Q3_K_M",
    )
    patch_config_for_local(cfg, info)
    local = next(m for m in cfg["models"] if m["alias"] == "local")
    assert local["temperature"] == 1.0
    assert local["extra_params"]["top_k"] == 40
    assert local["extra_params"]["top_p"] == 0.95
    assert local["extra_params"]["frequency_penalty"] == 1.1
    assert local["extra_params"]["max_tokens"] == 2048


def test_patch_bakes_context_window_for_llama_cpp():
    """When llama.cpp is detected, the local model should also pick up
    context_window=32768 (matches `-c 32768` from the article recipe)
    and auto_compact_threshold=28000. This ensures fresh installs and
    upgrades don't bloat past the server's context limit."""
    cfg = {
        "providers": [{"name": "llamacpp", "api_base": "http://127.0.0.1:8080/v1"}],
        "models": [
            {"alias": "local", "name": "p", "provider": "llamacpp"},
        ],
    }
    info = LocalServerInfo(
        label="llama.cpp",
        api_base="http://127.0.0.1:8000/v1",
        model_name="gemma-4-26B-A4B-it-Q3_K_M",
    )
    patch_config_for_local(cfg, info)
    local = next(m for m in cfg["models"] if m["alias"] == "local")
    assert local["context_window"] == 32768
    assert local["auto_compact_threshold"] == 28000


def test_patch_does_not_overwrite_user_extra_params():
    """If the user already configured extra_params, the llama.cpp
    detector adds missing keys but keeps user values."""
    cfg = {
        "providers": [{"name": "llamacpp", "api_base": "http://127.0.0.1:8080/v1"}],
        "models": [
            {"alias": "local", "name": "p", "provider": "llamacpp",
             "temperature": 0.5,
             "extra_params": {"top_k": 64, "min_p": 0.05}},
        ],
    }
    info = LocalServerInfo(
        label="llama.cpp",
        api_base="http://127.0.0.1:8080/v1",
        model_name="gemma-4-26B-A4B-it-Q3_K_M",
    )
    patch_config_for_local(cfg, info)
    local = next(m for m in cfg["models"] if m["alias"] == "local")
    # User-set values preserved
    assert local["temperature"] == 0.5
    assert local["extra_params"]["top_k"] == 64
    assert local["extra_params"]["min_p"] == 0.05
    # Missing keys filled in with article defaults
    assert local["extra_params"]["top_p"] == 0.95
    assert local["extra_params"]["frequency_penalty"] == 1.1
    assert local["extra_params"]["max_tokens"] == 2048


def test_patch_does_not_inject_for_non_llama_backends():
    """vLLM detection should NOT bake in the llama.cpp-specific recipe."""
    cfg = {
        "providers": [{"name": "llamacpp", "api_base": "http://127.0.0.1:8080/v1"}],
        "models": [
            {"alias": "local", "name": "p", "provider": "llamacpp"},
        ],
    }
    info = LocalServerInfo(
        label="vLLM",
        api_base="http://127.0.0.1:8000/v1",
        model_name="gemma4",
    )
    patch_config_for_local(cfg, info)
    local = next(m for m in cfg["models"] if m["alias"] == "local")
    # No temperature override, no extra_params injection
    assert "temperature" not in local
    assert local.get("extra_params", {}) == {} or "extra_params" not in local


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

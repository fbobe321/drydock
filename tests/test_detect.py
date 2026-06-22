"""Tests for first-launch local-LLM autodetection."""
from __future__ import annotations

import json
import urllib.error

from drydock import detect


class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_probe_parses_openai_shape(monkeypatch):
    monkeypatch.setattr(
        detect.urllib.request, "urlopen",
        lambda url, timeout=0.5: _FakeResp(200, {"data": [{"id": "gemma4"}]}),
    )
    assert detect.probe_endpoint("http://localhost:8000/v1") == ["gemma4"]


def test_probe_parses_ollama_shape(monkeypatch):
    monkeypatch.setattr(
        detect.urllib.request, "urlopen",
        lambda url, timeout=0.5: _FakeResp(200, {"models": [{"name": "llama3"}]}),
    )
    assert detect.probe_endpoint("http://localhost:11434/v1") == ["llama3"]


def test_probe_returns_none_on_connection_error(monkeypatch):
    def boom(url, timeout=0.5):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(detect.urllib.request, "urlopen", boom)
    assert detect.probe_endpoint("http://localhost:9999/v1") is None


def test_detect_returns_reachable_in_preference_order(monkeypatch):
    # Only the ollama port answers.
    def fake_urlopen(url, timeout=0.5):
        if "11434" in url:
            return _FakeResp(200, {"models": [{"name": "llama3"}]})
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(detect.urllib.request, "urlopen", fake_urlopen)
    found = detect.detect_local_llms()
    assert len(found) == 1
    assert found[0]["provider"] == "ollama"
    assert found[0]["models"] == ["llama3"]


def test_onboarding_message_when_found():
    msg = detect.onboarding_message(
        [{"provider": "vllm", "base_url": "http://localhost:8000/v1", "models": ["gemma4"]}]
    )
    assert "Detected vllm" in msg and "gemma4" in msg


def test_onboarding_message_when_nothing_found():
    msg = detect.onboarding_message([])
    assert "No local LLM detected" in msg
    assert "config.toml" in msg
    # New users must learn how to point Drydock at their model from the TUI.
    assert "/model url" in msg and "/model" in msg

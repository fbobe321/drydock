"""Tests for the unreachable-LLM error path."""
from __future__ import annotations

import httpx
import openai
import pytest

from drydock.providers import (
    LLMUnreachable,
    _friendly_unreachable,
    _safe_create,
)


class _DeadClient:
    """Stand-in OpenAI client whose create() raises a connection error."""

    class chat:  # noqa: N801 — mirrors the openai client attribute path
        class completions:
            @staticmethod
            def create(**kwargs):
                raise openai.APIConnectionError(
                    request=httpx.Request("POST", "http://localhost:9999/v1")
                )


def test_safe_create_maps_connection_error():
    with pytest.raises(LLMUnreachable) as ei:
        _safe_create(_DeadClient(), {}, "http://localhost:9999/v1", "vllm")
    msg = str(ei.value)
    assert "http://localhost:9999/v1" in msg
    assert "vllm" in msg
    assert "server is running" in msg  # remediation step present


def test_friendly_message_has_three_steps():
    msg = _friendly_unreachable("http://x/v1", "ollama")
    assert msg.count("\n") >= 3  # header + 3 numbered steps
    assert "config.toml" in msg
    assert "--base-url" in msg


def test_safe_create_passes_through_success():
    class _OK:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return "ok"

    assert _safe_create(_OK(), {}, "url", "vllm") == "ok"

"""Tests for the unreachable-LLM error path."""
from __future__ import annotations

import httpx
import openai
import pytest

from drydock.providers import (
    LLMUnreachable,
    _create_abortable,
    _friendly_timeout,
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


# ── Read-timeout must NOT masquerade as "server unreachable" ───────────────
# Regression: a slow local model doing a long (non-streaming) reasoning turn
# overran the 600s read timeout; openai raised APITimeoutError (a subclass of
# APIConnectionError) which got mapped to the misleading "Cannot reach the LLM"
# message. The server was fine — the request just ran long. APITimeoutError must
# be caught FIRST and produce the accurate "timed out / reachable" message.

class _TimeoutClient:
    class chat:  # noqa: N801
        class completions:
            @staticmethod
            def create(**kwargs):
                raise openai.APITimeoutError(
                    request=httpx.Request("POST", "http://localhost:8000/v1")
                )


def test_safe_create_maps_timeout_distinctly():
    with pytest.raises(LLMUnreachable) as ei:
        _safe_create(_TimeoutClient(), {}, "http://localhost:8000/v1", "vllm", 1800.0)
    msg = str(ei.value)
    assert "reachable" in msg          # NOT "cannot reach"
    assert "read timeout" in msg
    assert "1800" in msg               # surfaces the actual limit
    assert "Cannot reach the LLM" not in msg


def test_create_abortable_maps_timeout_distinctly():
    # the active path for Gemma (non-streaming) routes through _create_abortable
    with pytest.raises(LLMUnreachable) as ei:
        _create_abortable(_TimeoutClient(), {}, "http://localhost:8000/v1", "vllm", None, 1800.0)
    assert "read timeout" in str(ei.value)
    assert "Cannot reach the LLM" not in str(ei.value)


def test_friendly_timeout_renders_minutes_and_remediation():
    msg = _friendly_timeout("http://x/v1", 1800.0)
    assert "30 min" in msg
    assert "request_timeout" in msg     # how to raise the limit
    assert "reasoning budget" in msg    # how to shorten turns

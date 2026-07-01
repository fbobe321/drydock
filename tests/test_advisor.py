"""Second-model advisor: consult() + the Consult tool + config."""
from __future__ import annotations

import drydock.tools  # noqa: F401 — registers tools
from drydock import advisor
from drydock import tool_registry as reg


def test_is_configured():
    assert not advisor.is_configured({})
    assert not advisor.is_configured({"advisor_base_url": "http://x/v1"})   # no model
    assert advisor.is_configured({"advisor_base_url": "http://x/v1", "advisor_model": "g"})


def test_consult_unconfigured_gives_setup_help():
    out = advisor.consult("anything", {})
    assert "No advisor" in out and "/advisor" in out


def test_consult_empty_question():
    assert "nothing to ask" in advisor.consult("  ", {"advisor_base_url": "http://x/v1", "advisor_model": "g"})


def test_consult_calls_endpoint_and_returns_answer(monkeypatch):
    captured = {}

    class _Msg:  # noqa: D401
        content = "Use a mutex."

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _FakeClient:
        def __init__(self, **kw): captured["client"] = kw
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kw):
                    captured["call"] = kw
                    return _Resp()

    monkeypatch.setattr(advisor, "OpenAI", _FakeClient, raising=False)
    # advisor imports OpenAI lazily; patch the module symbol it will bind
    import openai
    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    cfg = {"advisor_base_url": "http://box:9/v1", "advisor_model": "gemini-x", "advisor_api_key": "k"}
    out = advisor.consult("How to fix this race?", cfg, context="def f(): ...")
    assert out == "Use a mutex."
    assert captured["client"]["base_url"] == "http://box:9/v1"
    assert captured["call"]["model"] == "gemini-x"
    # system + context + question all present
    roles = [m["role"] for m in captured["call"]["messages"]]
    assert roles == ["system", "user", "user"]


def test_consult_tool_unconfigured():
    out = reg.execute("Consult", {"question": "hi"}, {})
    assert "No advisor" in out
    assert "needs a `question`" in reg.execute("Consult", {}, {})


def test_consult_tool_is_read_only():
    assert reg.get("Consult").read_only is True


def test_test_connection_success(monkeypatch):
    monkeypatch.setattr(advisor, "_call", lambda *a, **k: "OK")
    out = advisor.test_connection({"advisor_base_url": "http://b/v1", "advisor_model": "m"})
    assert out.startswith("✓") and "m" in out and "responded" in out


def test_test_connection_failure(monkeypatch):
    def boom(*a, **k): raise OSError("connection refused")
    monkeypatch.setattr(advisor, "_call", boom)
    out = advisor.test_connection({"advisor_base_url": "http://b/v1", "advisor_model": "m"})
    assert out.startswith("✗") and "unreachable" in out and "connection refused" in out


def test_test_connection_unconfigured():
    assert "No advisor" in advisor.test_connection({})


def test_test_connection_timeout_says_reachable_but_slow(monkeypatch):
    def slow(*a, **k): raise TimeoutError("Request timed out.")
    monkeypatch.setattr(advisor, "_call", slow)
    out = advisor.test_connection({"advisor_base_url": "http://b/v1", "advisor_model": "m"})
    assert out.startswith("✗") and "REACHABLE but slow" in out and "/ask" in out

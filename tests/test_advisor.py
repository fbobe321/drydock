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


# ── recent-context auto-enrichment (advisor gets info even w/ thin caller context) ──
def test_recent_context_formats_and_keeps_newest():
    msgs = [
        {"role": "system", "content": "SYS should be skipped"},
        {"role": "assistant", "content": "I'll run the tests."},
        {"role": "tool", "content": "FAILED test_x - AssertionError: expected 42"},
        {"role": "assistant", "content": "The assertion failed; investigating add()."},
    ]
    out = advisor.recent_context(msgs)
    assert "SYS should be skipped" not in out              # system dropped
    assert "FAILED test_x" in out and "investigating add()" in out
    assert "[tool]" in out and "[assistant]" in out


def test_recent_context_truncates_to_recent_chars():
    msgs = [{"role": "assistant", "content": "x" * 500} for _ in range(40)]
    out = advisor.recent_context(msgs, max_chars=1000)
    assert len(out) <= 1001 and out.startswith("…")        # kept the tail


def test_recent_context_handles_multimodal_blocks():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "look at this"}, {"type": "image"}]}]
    assert "look at this" in advisor.recent_context(msgs)


def test_build_brief_combines_transcript_and_context():
    cfg = {"_recent_messages": [
        {"role": "tool", "content": "FAILED test_reconnect - state lost after reconnect"},
        {"role": "assistant", "content": "Trying a lock around the queue."},
    ]}
    brief = advisor.build_brief(cfg, "my hand-written context")
    assert "FAILED test_reconnect" in brief and "lock around the queue" in brief
    assert "my hand-written context" in brief
    assert "RECENT ACTIVITY" in brief and "CONTEXT" in brief


def test_consult_briefs_advisor_from_transcript_even_with_no_context(monkeypatch):
    # the whole point: a bare question still reaches the advisor WITH the recent transcript
    captured = {}
    monkeypatch.setattr(advisor, "_call",
                        lambda q, cfg, context="", **k: captured.update(q=q, context=context) or "ok")
    cfg = {"advisor_base_url": "http://b/v1", "advisor_model": "m",
           "_recent_messages": [{"role": "tool", "content": "FAILED test_x - AssertionError"}]}
    advisor.consult("why is this failing?", cfg)         # no context passed
    assert "FAILED test_x" in captured["context"]         # transcript auto-attached
    assert captured["q"] == "why is this failing?"


def test_consult_tool_delegates_to_consult(monkeypatch):
    from drydock import tools as T
    captured = {}
    monkeypatch.setattr(advisor, "consult",
                        lambda q, cfg, context="": captured.update(q=q, context=context) or "ok")
    T.tool_consult({"question": "help", "context": "hand-written"}, {"advisor_model": "m"})
    assert captured["q"] == "help" and captured["context"] == "hand-written"

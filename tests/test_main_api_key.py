"""Main-model API key in config.toml (mirrors advisor_api_key)."""
from __future__ import annotations

from drydock import config as C


def test_api_key_is_a_declared_default():
    # the fix: api_key is a first-class default (so it appears in generated config.toml
    # and survives resolve), not just something providers.py happened to read.
    assert "api_key" in C.DEFAULTS and C.DEFAULTS["api_key"] == ""


def test_config_file_api_key_round_trips(tmp_path):
    p = tmp_path / "config.toml"
    C.save_file({**C.DEFAULTS, "api_key": "sk-main-123"}, p)
    assert "api_key" in p.read_text()                 # written to the file (discoverable)
    cfg = C.resolve({}, p)
    assert cfg["api_key"] == "sk-main-123"            # and resolved back


def test_generated_default_config_includes_api_key(tmp_path):
    p = tmp_path / "config.toml"
    C.resolve({}, p)                                  # non-existent -> writes DEFAULTS
    assert "api_key" in p.read_text()


def test_stream_sends_config_api_key_to_client(monkeypatch):
    import openai

    from drydock import providers as P
    captured: dict = {}

    class _Stop(Exception):
        pass

    class _FakeClient:
        def __init__(self, **kw):
            captured.update(kw)

        def __getattr__(self, _name):     # any use (client.chat...) aborts after construction
            raise _Stop()

    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    gen = P.stream("m", "sys", [{"role": "user", "content": "hi"}], [],
                   {"provider": "vllm", "base_url": "http://x/v1", "api_key": "sk-MAIN"})
    try:
        next(gen)
    except Exception:
        pass
    assert captured.get("api_key") == "sk-MAIN"       # main-model key reaches the client


def test_stream_empty_api_key_falls_back_not_crashes(monkeypatch):
    import openai

    from drydock import providers as P
    captured: dict = {}

    class _Stop(Exception):
        pass

    class _FakeClient:
        def __init__(self, **kw):
            captured.update(kw)

        def __getattr__(self, _name):
            raise _Stop()

    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    gen = P.stream("m", "sys", [{"role": "user", "content": "hi"}], [],
                   {"provider": "vllm", "base_url": "http://x/v1", "api_key": ""})
    try:
        next(gen)
    except Exception:
        pass
    assert captured.get("api_key") == "dummy"         # empty -> provider default (unchanged behavior)

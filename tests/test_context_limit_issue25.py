"""GitHub issue #25: a server whose per-request n_ctx (e.g. 32768 from -c/-np slot
division) is smaller than the configured context_limit (e.g. 64K) hit a 400 and
looped ('already compact, nothing to free'). Fix: learn the server's real n_ctx from
the 400, ADOPT it as the runtime limit (self-heal), inform the user, and retry."""
from __future__ import annotations

from drydock import agent
from drydock.compaction import extract_server_n_ctx
from drydock.providers import AssistantTurn


def test_extract_n_ctx_from_real_error():
    err = ("400 - {'error': {'message': 'request (32830 tokens) exceeds the available "
           "context size (32768 tokens)', 'n_prompt_tokens': 32830, 'n_ctx': 32768}}")
    assert extract_server_n_ctx(err) == 32768
    assert extract_server_n_ctx("exceeds the available context size (16384 tokens)") == 16384
    assert extract_server_n_ctx("some unrelated error") is None


def _run_with_400(cfg):
    calls = {"n": 0}
    def fake(model, system, messages, tool_schemas, config):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("400 - {'error': {'message': 'request (32830 tokens) exceeds the "
                            "available context size (32768 tokens)', 'n_ctx': 32768}}")
        yield AssistantTurn("done", [], 5, 5)
    orig = agent.stream; agent.stream = fake
    try:
        text = "".join(ev.text for ev in agent.run("go", agent.AgentState(), cfg, "SYS")
                        if hasattr(ev, "text"))
        return text, calls["n"]
    finally:
        agent.stream = orig


def test_400_adopts_server_ctx_and_recovers():
    cfg = {"model": "gemma4", "context_limit": 65536, "max_tokens": 8192}
    text, n = _run_with_400(cfg)
    assert cfg["context_limit"] == 32768        # self-healed to the real per-request window
    assert n == 2                               # retried after compaction and succeeded
    assert "PER REQUEST" in text and "-np" in text   # diagnosed the parallel-slot cause


def test_400_does_not_raise_config_when_already_smaller():
    # server n_ctx (32768) >= configured (16384) → don't inflate the user's limit
    cfg = {"model": "gemma4", "context_limit": 16384, "max_tokens": 8192}
    _run_with_400(cfg)
    assert cfg["context_limit"] == 16384        # unchanged (theirs was already tighter)

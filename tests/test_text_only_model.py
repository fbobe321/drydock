"""Text-only model fallback: when the server rejects image input because the model
isn't multimodal (vLLM "X is not a multimodal model", llama.cpp "image input is not
supported"), the agent stops attaching images for that endpoint and retries the step
as plain text instead of dead-ending the turn on a raw 400."""
from __future__ import annotations

from drydock import agent, providers
from drydock.agent import AgentState
from drydock.compaction import is_image_load_error, is_text_only_model_error
from drydock.providers import AssistantTurn, messages_to_openai

from tests.test_vision_input import _write_png

VLLM_ERR = ("Error code: 400 - {'error': {'message': 'nemotron is not a multimodal model', "
            "'type': 'BadRequestError', 'param': None, 'code': 400}}")
LLAMA_ERR = ("Error code: 400 - {'error': {'code': 400, 'message': 'image input is not supported "
             "- hint: if this is unexpected, you may need to provide the mmproj', "
             "'type': 'invalid_request_error'}}")


def test_classifies_text_only_errors():
    assert is_text_only_model_error(VLLM_ERR)
    assert is_text_only_model_error(LLAMA_ERR)
    assert not is_text_only_model_error("Failed to load image or audio file")
    assert not is_text_only_model_error("context_length_exceeded")
    # a corrupt image is still an image-load error, not a text-only model
    assert is_image_load_error("Failed to load image or audio file")


def test_vision_false_keeps_image_reference_as_text(tmp_path):
    p = _write_png(tmp_path / "board.png")
    msgs = messages_to_openai([{"role": "user", "content": f"see {p}"}], "sys", vision=False)
    assert msgs[1]["content"] == f"see {p}"
    tool = messages_to_openai([{"role": "tool", "tool_call_id": "c", "name": "ViewImage",
                                "content": f"viewing {p}"}], "sys", vision=False)
    assert tool[1]["content"] == f"viewing {p}"


def test_agent_drops_images_and_retries(monkeypatch, tmp_path):
    p = _write_png(tmp_path / "board.png")
    cfg = {"model": "textonly-test", "base_url": "http://tx.invalid:9/v1",
           "max_tokens": 1024, "context_limit": 65536}
    calls = []

    def fake_stream(model, system, messages, tool_schemas, config):
        vision = providers.vision_enabled(config)
        calls.append(vision)
        if vision:
            raise RuntimeError(VLLM_ERR)
        yield AssistantTurn(text="done", tool_calls=[], input_tokens=10, output_tokens=5)

    monkeypatch.setattr(agent, "stream", fake_stream)
    monkeypatch.setattr(providers, "_TEXT_ONLY", set())
    events = list(agent.run(f"what is in {p}", AgentState(), cfg, "SYS"))
    assert calls == [True, False]
    assert any("text-only" in getattr(e, "text", "") for e in events)
    assert not providers.vision_enabled(cfg)
    # a different endpoint is unaffected
    assert providers.vision_enabled({**cfg, "base_url": "http://other.invalid:9/v1"})


def test_second_text_only_error_raises_not_loops(monkeypatch):
    cfg = {"model": "textonly-test2", "base_url": "http://tx2.invalid:9/v1",
           "max_tokens": 1024, "context_limit": 65536}
    calls = []

    def fake_stream(model, system, messages, tool_schemas, config):
        calls.append(1)
        raise RuntimeError(VLLM_ERR)
        yield  # pragma: no cover

    monkeypatch.setattr(agent, "stream", fake_stream)
    monkeypatch.setattr(providers, "_TEXT_ONLY", set())
    try:
        list(agent.run("hi", AgentState(), cfg, "SYS"))
    except RuntimeError:
        pass
    assert len(calls) == 2

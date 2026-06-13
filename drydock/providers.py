"""LLM provider abstraction — works with any OpenAI-compatible endpoint.

Supports: vLLM, Ollama, LM Studio, OpenAI, Anthropic, or any custom endpoint.
Uses a neutral message format internally, converts per-provider on the wire.

DryDock v3 — clean rewrite, no model-specific tool call parsers.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Generator

from drydock.tuning import strip_leaked_tool_calls, strip_thinking_tokens, use_streaming

# ── Provider registry ─────────────────────────────────────────────────────

PROVIDERS: dict[str, dict] = {
    "vllm": {
        "type": "openai",
        "base_url": "http://localhost:8000/v1",
        "api_key": "dummy",
        "context_limit": 131072,
    },
    "ollama": {
        "type": "openai",
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "context_limit": 131072,
    },
    "lmstudio": {
        "type": "openai",
        "base_url": "http://localhost:1234/v1",
        "api_key": "lm-studio",
        "context_limit": 131072,
    },
    "openai": {
        "type": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "context_limit": 128000,
    },
}


class LLMUnreachable(RuntimeError):
    """The configured LLM endpoint could not be reached. Carries a
    user-facing message with remediation steps (shown verbatim in the TUI)."""


def _friendly_unreachable(base_url: str, provider: str) -> str:
    return (
        f"Cannot reach the LLM at {base_url} (provider: {provider}).\n"
        f"  1. Make sure your model server is running and listening on that port.\n"
        f"  2. Wrong URL? Override with --base-url, or set base_url in "
        f"~/.drydock/config.toml.\n"
        f"  3. Start a local server (llama.cpp / vLLM / Ollama / LM Studio) on "
        f"that port, then send your message again."
    )


def _safe_create(client, kwargs: dict, base_url: str, provider: str):
    """Call chat.completions.create, mapping a connection failure to a clean
    LLMUnreachable instead of a raw traceback / 12-minute hang."""
    import openai

    try:
        return client.chat.completions.create(**kwargs)
    except openai.APIConnectionError as e:
        raise LLMUnreachable(_friendly_unreachable(base_url, provider)) from e


# ── Event types ───────────────────────────────────────────────────────────

@dataclass
class TextChunk:
    text: str

@dataclass
class AssistantTurn:
    text: str
    tool_calls: list  # [{id, name, input}, ...]
    input_tokens: int
    output_tokens: int
    had_leaked_call: bool = False  # model emitted a tool call as text, not a call


# ── Message format conversion ─────────────────────────────────────────────

def messages_to_openai(messages: list, system: str) -> list:
    """Convert neutral messages to OpenAI API format."""
    result = [{"role": "system", "content": system}]
    for m in messages:
        role = m["role"]
        if role == "user":
            result.append({"role": "user", "content": m["content"]})
        elif role == "assistant":
            msg = {"role": "assistant", "content": m.get("content") or None}
            tcs = m.get("tool_calls", [])
            if tcs:
                msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["input"], ensure_ascii=False),
                        },
                    }
                    for tc in tcs
                ]
            result.append(msg)
        elif role == "tool":
            result.append({
                "role": "tool",
                "tool_call_id": m["tool_call_id"],
                "content": m["content"],
            })
    return result


def tools_to_openai(tool_schemas: list) -> list:
    """Convert tool schemas to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tool_schemas
    ]


# ── Streaming ─────────────────────────────────────────────────────────────

def stream(
    model: str,
    system: str,
    messages: list,
    tool_schemas: list,
    config: dict,
) -> Generator:
    """Stream from any OpenAI-compatible API.

    Yields TextChunk during streaming, then AssistantTurn at the end.
    Works with vLLM, Ollama, LM Studio, OpenAI, or any compatible endpoint.
    """
    import httpx
    from openai import OpenAI

    provider = config.get("provider", "vllm")
    prov = PROVIDERS.get(provider, PROVIDERS["vllm"])

    base_url = config.get("base_url") or prov.get("base_url", "http://localhost:8000/v1")
    api_key = config.get("api_key") or prov.get("api_key") or os.environ.get(
        prov.get("api_key_env", ""), "dummy"
    )

    # Fail fast on a dead endpoint: a 10s connect timeout and no retries surface
    # an unreachable server in seconds. Generation itself can still take minutes
    # (long read timeout).
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=0,
        timeout=httpx.Timeout(600.0, connect=10.0),
    )

    oai_messages = messages_to_openai(messages, system)

    has_tools = bool(tool_schemas)
    # Gemma corrupts tool-call JSON when streamed; force non-streaming on tool
    # turns. config["force_stream"] overrides for debugging.
    do_stream = config.get("force_stream") or use_streaming(model, has_tools)

    kwargs = {
        "model": model,
        "messages": oai_messages,
        "stream": do_stream,
    }
    if do_stream:
        kwargs["stream_options"] = {"include_usage": True}

    if tool_schemas:
        kwargs["tools"] = tools_to_openai(tool_schemas)
        kwargs["tool_choice"] = config.get("tool_choice", "auto")

    if config.get("max_tokens"):
        kwargs["max_tokens"] = config["max_tokens"]

    if config.get("temperature") is not None:
        kwargs["temperature"] = config["temperature"]

    # Adaptive reasoning budget (harmony/gpt-oss style models accept this;
    # endpoints without the knob ignore the extra field). Set per turn by the
    # agent loop: high for planning, low for routine continuation.
    if config.get("reasoning_effort"):
        kwargs["reasoning_effort"] = config["reasoning_effort"]

    if not do_stream:
        # Non-streaming: one request, parse the complete message. This is the
        # reliable path for local models whose tool-call JSON breaks under
        # streaming.
        yield from _complete_nonstreaming(client, kwargs, base_url, provider)
        return

    text = ""
    tool_buf: dict = {}  # index → {id, name, args}
    in_tok = out_tok = 0

    response = _safe_create(client, kwargs, base_url, provider)
    for chunk in response:
        if not chunk.choices:
            if hasattr(chunk, "usage") and chunk.usage:
                in_tok = chunk.usage.prompt_tokens or in_tok
                out_tok = chunk.usage.completion_tokens or out_tok
            continue

        choice = chunk.choices[0]
        delta = choice.delta

        if delta.content:
            # Strip any leaked thinking-token markers (best-effort per chunk).
            chunk_text = strip_thinking_tokens(delta.content)
            if chunk_text.strip():
                text += chunk_text
                yield TextChunk(chunk_text)

        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_buf:
                    tool_buf[idx] = {"id": "", "name": "", "args": ""}
                if tc.id:
                    tool_buf[idx]["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        tool_buf[idx]["name"] += tc.function.name
                    if tc.function.arguments:
                        tool_buf[idx]["args"] += tc.function.arguments

        if hasattr(chunk, "usage") and chunk.usage:
            in_tok = chunk.usage.prompt_tokens or in_tok
            out_tok = chunk.usage.completion_tokens or out_tok

    tool_calls = []
    for idx in sorted(tool_buf):
        v = tool_buf[idx]
        try:
            inp = json.loads(v["args"]) if v["args"] else {}
        except json.JSONDecodeError:
            inp = {"_raw": v["args"]}
        tool_calls.append({
            "id": v["id"] or f"call_{idx}",
            "name": v["name"],
            "input": inp,
        })

    yield AssistantTurn(text, tool_calls, in_tok, out_tok)


def _complete_nonstreaming(
    client, kwargs: dict, base_url: str = "", provider: str = ""
) -> Generator:
    """Single non-streaming completion. Yields one TextChunk (if any text)
    then the AssistantTurn. Used when streaming would corrupt tool-call JSON.
    """
    resp = _safe_create(client, kwargs, base_url, provider)
    choice = resp.choices[0]
    msg = choice.message

    # Order matters: remove <|tool_call> blobs FIRST (sets had_leak for the
    # retry nudge), THEN strip channel/special-token markers — otherwise the
    # generic special-token pass would eat the tool_call markers.
    text, had_leak = strip_leaked_tool_calls(msg.content or "")
    text = strip_thinking_tokens(text)
    if text.strip():
        yield TextChunk(text)

    tool_calls = []
    for i, tc in enumerate(getattr(msg, "tool_calls", None) or []):
        raw_args = tc.function.arguments or ""
        try:
            inp = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            inp = {"_raw": raw_args}
        tool_calls.append({
            "id": tc.id or f"call_{i}",
            "name": tc.function.name,
            "input": inp,
        })

    usage = getattr(resp, "usage", None)
    in_tok = usage.prompt_tokens if usage else 0
    out_tok = usage.completion_tokens if usage else 0

    # Only flag a leak when the blob did NOT also produce real structured
    # calls — if the model gave us a usable call, there's nothing to recover.
    yield AssistantTurn(
        text, tool_calls, in_tok, out_tok,
        had_leaked_call=had_leak and not tool_calls,
    )

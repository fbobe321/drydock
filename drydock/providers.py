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
    from openai import OpenAI

    provider = config.get("provider", "vllm")
    prov = PROVIDERS.get(provider, PROVIDERS["vllm"])

    base_url = config.get("base_url") or prov.get("base_url", "http://localhost:8000/v1")
    api_key = config.get("api_key") or prov.get("api_key") or os.environ.get(
        prov.get("api_key_env", ""), "dummy"
    )

    client = OpenAI(api_key=api_key, base_url=base_url)

    oai_messages = messages_to_openai(messages, system)

    kwargs = {
        "model": model,
        "messages": oai_messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    if tool_schemas:
        kwargs["tools"] = tools_to_openai(tool_schemas)
        kwargs["tool_choice"] = config.get("tool_choice", "auto")

    if config.get("max_tokens"):
        kwargs["max_tokens"] = config["max_tokens"]

    if config.get("temperature") is not None:
        kwargs["temperature"] = config["temperature"]

    text = ""
    tool_buf: dict = {}  # index → {id, name, args}
    in_tok = out_tok = 0

    response = client.chat.completions.create(**kwargs)
    for chunk in response:
        if not chunk.choices:
            if hasattr(chunk, "usage") and chunk.usage:
                in_tok = chunk.usage.prompt_tokens or in_tok
                out_tok = chunk.usage.completion_tokens or out_tok
            continue

        choice = chunk.choices[0]
        delta = choice.delta

        if delta.content:
            # Filter Gemma 4 thinking tokens that leak through
            chunk_text = delta.content
            if "<|channel>" in chunk_text or "<channel|>" in chunk_text:
                import re
                chunk_text = re.sub(r'<\|channel>[^<]*<channel\|>', '', chunk_text)
                chunk_text = chunk_text.replace('<|channel>', '').replace('<channel|>', '')
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

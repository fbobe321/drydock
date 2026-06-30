"""LLM provider abstraction — works with any OpenAI-compatible endpoint.

Supports: vLLM, Ollama, LM Studio, OpenAI, Anthropic, or any custom endpoint.
Uses a neutral message format internally, converts per-provider on the wire.

DryDock v3 — clean rewrite, no model-specific tool call parsers.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
from dataclasses import dataclass
from typing import Generator

# Run blocking LLM calls here so STOP can ABANDON one mid-decode: closing the
# HTTP client does NOT interrupt an in-flight blocking read, so instead we wait
# on a future and, when the user cancels, return immediately and let the
# orphaned request finish (and be discarded) in the background.
_LLM_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="llm")


class _StopRequested(Exception):
    """Internal: STOP was pressed during a blocking LLM call."""

from drydock.loop_detect import runaway_repetition_len
from drydock.tuning import extract_thinking, strip_leaked_tool_calls, strip_thinking_tokens, use_streaming

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


def probe_server_context(base_url: str, *, timeout: float = 4.0) -> int | None:
    """Best-effort: ask the model server its REAL context window (tokens).
    llama.cpp exposes it on `/props` (n_ctx); vLLM puts `max_model_len` on
    `/v1/models`. Returns None if unknown/unreachable — never raises. This is the
    definitive diagnostic for a "stuck at N tokens" cap that isn't drydock's
    config (it's the server's own `-c` / `--ctx-size` / `max_model_len`)."""
    import json
    import urllib.request

    root = base_url.rstrip("/")
    base_no_v1 = root[:-3].rstrip("/") if root.endswith("/v1") else root

    def _n_ctx(d):
        return (d.get("default_generation_settings") or {}).get("n_ctx") or d.get("n_ctx")

    def _max_model_len(d):
        return next((m.get("max_model_len") for m in d.get("data", []) if m.get("max_model_len")), None)

    for url, pick in ((base_no_v1 + "/props", _n_ctx), (root + "/models", _max_model_len)):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (user's own server)
                data = json.loads(r.read().decode("utf-8", "ignore"))
            val = pick(data)
            if isinstance(val, int) and val > 0:
                return val
        except Exception:  # noqa: BLE001 — best-effort probe
            continue
    return None


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


def _friendly_timeout(base_url: str, timeout_s: float) -> str:
    """The server IS reachable but a single generation overran the read timeout.
    Common with slow local models doing long reasoning turns (a non-streaming
    response only arrives once the whole generation finishes). Distinct from
    'unreachable' so the user fixes the right thing."""
    mins = timeout_s / 60.0
    return (
        f"The model server at {base_url} is reachable, but the request exceeded "
        f"the {timeout_s:.0f}s ({mins:.0f} min) read timeout before finishing.\n"
        f"  This usually means the model is generating a very long response "
        f"(e.g. a big reasoning turn on a slow local model), not that the server "
        f"is down.\n"
        f"  1. Raise the limit: set request_timeout (seconds) in "
        f"~/.drydock/config.toml.\n"
        f"  2. Or shorten turns: lower the server's reasoning budget "
        f"(llama.cpp --reasoning-budget).\n"
        f"  Your last message was not lost — just send it again."
    )


def _safe_create(client, kwargs: dict, base_url: str, provider: str, timeout_s: float = 600.0):
    """Call chat.completions.create, mapping a connection failure to a clean
    LLMUnreachable instead of a raw traceback / 12-minute hang."""
    import openai

    try:
        return client.chat.completions.create(**kwargs)
    # APITimeoutError subclasses APIConnectionError — catch it FIRST so a slow
    # (but alive) server gets the accurate "timed out" message, not "down".
    except openai.APITimeoutError as e:
        raise LLMUnreachable(_friendly_timeout(base_url, timeout_s)) from e
    except openai.APIConnectionError as e:
        raise LLMUnreachable(_friendly_unreachable(base_url, provider)) from e


def _create_abortable(client, kwargs: dict, base_url: str, provider: str, cancel,
                      timeout_s: float = 600.0):
    """Like _safe_create, but runs off-thread and polls a cancel Event so STOP
    can abandon a blocked decode (raising _StopRequested). The orphaned request
    runs to completion in the pool thread and its result is dropped."""
    import openai

    fut = _LLM_POOL.submit(client.chat.completions.create, **kwargs)
    while True:
        try:
            return fut.result(timeout=0.2)
        except concurrent.futures.TimeoutError:
            if cancel is not None and cancel.is_set():
                raise _StopRequested
        # APITimeoutError subclasses APIConnectionError — catch it FIRST.
        except openai.APITimeoutError as e:
            raise LLMUnreachable(_friendly_timeout(base_url, timeout_s)) from e
        except openai.APIConnectionError as e:
            raise LLMUnreachable(_friendly_unreachable(base_url, provider)) from e


def _stream_chunks(response, cancel):
    """Yield streamed chunks, but pull them via a background thread + queue so a
    STOP can abandon a read that's blocked waiting for the first token (the
    thinking phase). On cancel we just stop reading; the orphaned producer
    drains in the background."""
    import queue as _queue

    q: _queue.Queue = _queue.Queue()
    _DONE = object()

    def _produce():
        try:
            for ch in response:
                q.put(ch)
        except Exception as e:  # noqa: BLE001 — surface to the consumer
            q.put(e)
        finally:
            q.put(_DONE)

    _LLM_POOL.submit(_produce)
    while True:
        if cancel is not None and cancel.is_set():
            return
        try:
            item = q.get(timeout=0.2)
        except _queue.Empty:
            continue
        if item is _DONE:
            return
        if isinstance(item, Exception):
            raise item
        yield item


# ── Event types ───────────────────────────────────────────────────────────

@dataclass
class TextChunk:
    text: str

@dataclass
class ReasoningChunk:
    """Thinking tokens emitted by the model before its answer (Gemma 4 only).
    Yielded once per non-streaming turn, before any TextChunk, when the model
    included a <|channel>…<channel|> block in its response.
    """
    text: str

@dataclass
class AssistantTurn:
    text: str
    tool_calls: list  # [{id, name, input}, ...]
    input_tokens: int
    output_tokens: int
    had_leaked_call: bool = False  # model emitted a tool call as text, not a call


# ── Tool-call argument parsing ────────────────────────────────────────────

def _parse_tool_args(raw: str) -> dict:
    """Parse a model's tool-call arguments tolerantly.

    strict=False is the fix that matters: a Bash command built with a heredoc
    (`cat <<EOF ...`) carries LITERAL newlines, which strict JSON rejects with
    "Invalid control character". The call then degraded to {"_raw": ...} with no
    usable args, Bash failed, and the model's corrections re-wrapped into nested
    {"_raw": "{\"_raw\": ...}"} — a fatal loop. strict=False accepts control
    chars inside strings (recovering the common multiline case); we also unwrap
    a few layers of nested {"_raw": ...} the model may have echoed back.
    """
    if not raw:
        return {}
    for _ in range(4):  # peel nested _raw layers, then parse
        try:
            v = json.loads(raw, strict=False)
        except (json.JSONDecodeError, TypeError):
            return {"_raw": raw}
        if isinstance(v, dict) and set(v) == {"_raw"} and isinstance(v["_raw"], str):
            raw = v["_raw"]
            continue
        return v if isinstance(v, dict) else {"_raw": raw}
    return {"_raw": raw}


# ── Message format conversion ─────────────────────────────────────────────

_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}


def detect_image_paths(content) -> list[str]:
    """Image file paths referenced in a user message that exist on disk. Shared
    by the multimodal attach (below) and the TUI (to confirm '📎 attached')."""
    if not isinstance(content, str) or "." not in content:
        return []
    import os
    import re

    exts = "png|jpe?g|gif|webp|bmp"
    cands = re.findall(
        rf'"([^"]+?\.(?:{exts}))"|\'([^\']+?\.(?:{exts}))\'|(\S+\.(?:{exts}))',
        content, re.IGNORECASE,
    )
    seen: list[str] = []
    for tup in cands:
        raw = next((x for x in tup if x), None)
        if not raw:
            continue
        # the greedy \S+ branch grabs surrounding markdown/punctuation, e.g. a
        # path in backticks `/app/code.png`, parens, or with trailing .,;: —
        # strip it so os.path.isfile sees the real path (else vision silently
        # never attaches).
        raw = raw.strip("`'\"()[]{}<>").rstrip(".,;:!?")
        p = os.path.expanduser(raw)
        if os.path.isfile(p) and p not in seen:
            seen.append(p)
    return seen


_MAX_IMAGE_BYTES = 20_000_000  # don't base64 a >20MB file into a request


def _image_url_block(path: str) -> dict | None:
    """A base64 data-URL image_url block for an on-disk image, or None if it
    can't be read / is too large."""
    import base64
    import os

    try:
        if os.path.getsize(path) > _MAX_IMAGE_BYTES:
            return None
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except OSError:
        return None
    mime = _IMAGE_MIME.get(os.path.splitext(path)[1].lower(), "image/png")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def _content_with_images(content):
    """Turn text that references on-disk image paths into a multimodal content
    list ([text, image_url…]); text without resolvable images passes through
    unchanged. Used for BOTH user messages (the user names an image) and
    ViewImage tool results (the AGENT chose to view one) — built ONLY here at the
    API boundary so display/compaction/token-counting still see plain strings."""
    seen = detect_image_paths(content)
    if not seen:
        return content
    blocks: list[dict] = [{"type": "text", "text": content}]
    for p in seen:
        block = _image_url_block(p)
        if block:
            blocks.append(block)
    return blocks if len(blocks) > 1 else content


# Back-compat alias (user-message vision).
_user_content_with_images = _content_with_images


def messages_to_openai(messages: list, system: str) -> list:
    """Convert neutral messages to OpenAI API format."""
    # value type is mixed (str content, multimodal list content, tool_calls list)
    result: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        role = m["role"]
        if role == "user":
            result.append({"role": "user",
                           "content": _user_content_with_images(m["content"])})
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
            content = m["content"]
            # Agent-side vision: a ViewImage result names an image the agent chose
            # to look at — attach it so the model SEES it (servers read images
            # from tool messages too, verified). Only ViewImage, so an unrelated
            # tool mentioning a .png path never balloons the request.
            if m.get("name") == "ViewImage" and isinstance(content, str):
                content = _content_with_images(content)
            result.append({
                "role": "tool",
                "tool_call_id": m["tool_call_id"],
                "content": content,
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
    # an unreachable server in seconds. Generation itself can take many minutes —
    # a slow local model doing a long reasoning turn (non-streaming, so the whole
    # response only lands once it finishes) can exceed 10 min. Default the read
    # timeout to 30 min and let the operator raise it via config request_timeout.
    try:
        read_timeout = float(config.get("request_timeout") or 1800.0)
    except (TypeError, ValueError):
        read_timeout = 1800.0
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=0,
        timeout=httpx.Timeout(read_timeout, connect=10.0),
    )
    # Expose the client so the TUI's STOP (Esc / Ctrl+C) can close it mid-call —
    # closing the connection aborts an in-flight blocking decode immediately
    # instead of waiting out the whole generation.
    cancel = config.get("_cancel")
    # Stash in the SHARED abort holder (a mutable dict that survives run()'s
    # dict(config) shallow-copy) so the TUI's STOP can reach this exact client.
    config.setdefault("_abort", {})["client"] = client

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
        # streaming. If STOP closes the client mid-call the blocking request
        # raises — swallow that as a clean cancel rather than an error.
        try:
            yield from _complete_nonstreaming(client, kwargs, base_url, provider, cancel, read_timeout)
        except _StopRequested:
            return  # STOP — abandon the in-flight request, clean stop
        except Exception:
            if cancel is not None and cancel.is_set():
                return
            raise
        finally:
            config.get("_abort", {}).pop("client", None)
        return

    text = ""
    tool_buf: dict = {}  # index → {id, name, args}
    in_tok = out_tok = 0
    # Runaway-repetition guard: a weak model can collapse into streaming one
    # short unit hundreds of times (gemma4: `295:` ×1365). Advisory, never
    # raises — on detection we trim the repeated tail, emit a note, and stop
    # reading (the orphaned producer drains in the background, like STOP).
    _rep_checked_at = 0

    try:
        response = _create_abortable(client, kwargs, base_url, provider, cancel, read_timeout)
    except _StopRequested:
        config.get("_abort", {}).pop("client", None)
        return
    for chunk in _stream_chunks(response, cancel):
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
            # Keep ANY non-empty chunk — including whitespace-only ones. A
            # `.strip()` guard here used to DROP newline-only chunks (the blank
            # line a model streams between markdown blocks arrives as its own
            # chunk), mashing "para### Header" together. `if chunk_text:` still
            # skips chunks that stripping emptied (e.g. a lone thinking marker).
            if chunk_text:
                text += chunk_text
                yield TextChunk(chunk_text)
                # Throttled: only scan once text is long enough and every ~200
                # new chars, so the common path pays almost nothing.
                if len(text) - _rep_checked_at >= 200:
                    _rep_checked_at = len(text)
                    run = runaway_repetition_len(text)
                    if run:
                        text = (
                            text[: len(text) - run]
                            + "\n[… output began repeating — stopped by drydock]"
                        )
                        yield TextChunk(
                            "\n[stopped — model output began repeating itself]"
                        )
                        break

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

    config.get("_abort", {}).pop("client", None)  # generation done — drop the abort handle
    if cancel is not None and cancel.is_set():
        return  # stopped mid-stream — don't emit a partial turn
    tool_calls = []
    for idx in sorted(tool_buf):
        v = tool_buf[idx]
        inp = _parse_tool_args(v["args"])
        tool_calls.append({
            "id": v["id"] or f"call_{idx}",
            "name": v["name"],
            "input": inp,
        })

    yield AssistantTurn(text, tool_calls, in_tok, out_tok)


def _complete_nonstreaming(
    client, kwargs: dict, base_url: str = "", provider: str = "", cancel=None,
    timeout_s: float = 600.0,
) -> Generator:
    """Single non-streaming completion. Yields one TextChunk (if any text)
    then the AssistantTurn. Used when streaming would corrupt tool-call JSON.
    Runs off-thread so STOP can abandon a blocked decode.
    """
    resp = _create_abortable(client, kwargs, base_url, provider, cancel, timeout_s)
    choice = resp.choices[0]
    msg = choice.message

    # Order matters: remove <|tool_call> blobs FIRST (sets had_leak for the
    # retry nudge), THEN extract+strip channel/special-token markers — otherwise
    # the generic special-token pass would eat the tool_call markers.
    text, had_leak = strip_leaked_tool_calls(msg.content or "")
    thinking, text = extract_thinking(text)
    if thinking:
        yield ReasoningChunk(thinking)
    if text.strip():
        yield TextChunk(text)

    tool_calls = []
    for i, tc in enumerate(getattr(msg, "tool_calls", None) or []):
        inp = _parse_tool_args(tc.function.arguments or "")
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

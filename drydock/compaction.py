"""Context window management — keep conversations within token limits.

DryDock v3 — layered compaction:
1. Truncate long tool results
2. Drop old tool results
3. Emergency mode: aggressive truncation on 400 errors
"""
from __future__ import annotations


# Substrings that mark a context-overflow 400 across providers/servers
# (llama.cpp, vLLM, Ollama, OpenAI all phrase it differently).
_CONTEXT_ERROR_HINTS = (
    "context length",
    "maximum context",
    "context window",
    "context size",
    "exceed context",
    "exceeds the context",
    "n_ctx",
    "too many tokens",
    "maximum number of tokens",
    "reduce the length",
)


def is_context_length_error(err: str) -> bool:
    """Whether a provider error looks like a context-overflow 400, so the
    agent loop knows to emergency-compact and retry rather than surface it."""
    e = (err or "").lower()
    if any(h in e for h in _CONTEXT_ERROR_HINTS):
        return True
    # Catch generic phrasings like "... exceeds the model's context ..." too.
    return "context" in e and ("exceed" in e or "too long" in e or "too large" in e)


def is_image_load_error(err: str) -> bool:
    """Whether a provider 400 is the server failing to decode an attached image
    (corrupt / truncated / unsupported), so the agent ends the turn with a clean
    message instead of dumping the raw API error."""
    e = (err or "").lower()
    return "failed to load image" in e or (
        "image" in e and ("invalid_request" in e or "could not" in e or "decode" in e)
    )


def estimate_tokens(messages: list) -> int:
    """Rough token estimate: chars / 3.5.

    CRUCIAL: a tool call's arguments live in a nested ``input`` dict — e.g. a
    full-file Write puts the whole file in tool_calls[].input.content. Counting
    only top-level strings missed that entirely, so a 40k-char Write read as ~0
    tokens, compaction never fired, and the real request blew past the context
    window. Count nested strings so the estimate reflects what's actually sent.
    """
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content)
        for tc in m.get("tool_calls", []) or []:
            total += _count_chars(tc)
    # /3.0, not /3.5: code is symbol-dense and tokenizes to MORE tokens than
    # prose, so a conservative (slightly high) estimate keeps us off the wall.
    return int(total / 3.0)


def _count_chars(v) -> int:
    """Total length of every string nested in v (str / dict / list)."""
    if isinstance(v, str):
        return len(v)
    if isinstance(v, dict):
        return sum(_count_chars(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return sum(_count_chars(x) for x in v)
    return 0


def _truncate_tool_call_args(messages: list, max_len: int) -> None:
    """Shrink oversized string arguments inside OLD assistant tool calls (a
    full-file Write/Edit is a top bloat source and nothing else truncates it).
    The most recent tool-call message is left intact; truncating only the
    argument *values* keeps each call's id, so tool-result pairing stays valid."""
    last = max((i for i, m in enumerate(messages) if m.get("tool_calls")), default=-1)
    for i, m in enumerate(messages):
        if i == last:
            continue
        for tc in m.get("tool_calls") or []:
            inp = tc.get("input") if isinstance(tc, dict) else None
            if isinstance(inp, dict):
                for k, v in inp.items():
                    if isinstance(v, str) and len(v) > max_len:
                        inp[k] = v[: max_len // 2] + "\n[... arg truncated ...]"


def compact(messages: list, context_limit: int = 131072) -> list:
    """Compact messages to fit within context limit.

    Strategy:
    1. First pass: truncate long tool results to 800 chars
    2. Second pass: drop oldest tool results if still over limit
    3. Always keep: first user message, last 8 messages
    """
    target = int(context_limit * 0.60)  # Leave 40% headroom

    # Pass 1: Truncate long tool results
    for m in messages:
        if m["role"] == "tool" and isinstance(m.get("content"), str):
            content = m["content"]
            if len(content) > 1500:
                head = 400
                tail = 300
                m["content"] = (
                    content[:head]
                    + f"\n[... {len(content) - head - tail} chars truncated ...]\n"
                    + content[-tail:]
                )

    current = estimate_tokens(messages)
    if current <= target:
        return messages

    # Pass 2: Drop old tool results (keep last 8 messages)
    keep_last = 8
    if len(messages) > keep_last + 2:
        droppable = []
        for i in range(1, len(messages) - keep_last):
            if messages[i]["role"] == "tool":
                droppable.append(i)

        # Drop OLDEST first so the most recent context survives. (Iterating in
        # ascending index order = oldest-to-newest.)
        for i in droppable:
            messages[i]["content"] = "[tool result removed]"
            current = estimate_tokens(messages)
            if current <= target:
                break

    # Pass 3: shrink big old tool-call arguments (full-file Write/Edit bodies).
    if estimate_tokens(messages) > target:
        _truncate_tool_call_args(messages, max_len=1500)

    return messages


def emergency_compact(messages: list, context_limit: int = 131072) -> list:
    """Aggressive compaction when we hit a 400 context-length error.

    Much more aggressive than normal compaction:
    1. Truncate ALL tool results to 300 chars
    2. Drop all tool results except last 4 messages
    3. Truncate old assistant text
    """
    target = int(context_limit * 0.50)  # Leave 50% headroom

    # Pass 1: Truncate ALL tool results aggressively
    for m in messages:
        if m["role"] == "tool" and isinstance(m.get("content"), str):
            content = m["content"]
            if len(content) > 300:
                m["content"] = content[:200] + "\n[... truncated ...]\n" + content[-80:]

    current = estimate_tokens(messages)
    if current <= target:
        return messages

    # Pass 2: Drop ALL old tool results except last 4 messages
    keep_last = 4
    if len(messages) > keep_last + 2:
        for i in range(1, len(messages) - keep_last):
            if messages[i]["role"] == "tool":
                messages[i]["content"] = "[removed]"

    current = estimate_tokens(messages)
    if current <= target:
        return messages

    # Pass 3: Truncate old assistant text
    if len(messages) > keep_last + 2:
        for i in range(1, len(messages) - keep_last):
            if messages[i]["role"] == "assistant":
                content = messages[i].get("content", "")
                if isinstance(content, str) and len(content) > 500:
                    messages[i]["content"] = content[:300] + "\n[... truncated ...]"

    # Pass 4: shrink big tool-CALL arguments — a full-file Write/Edit puts the
    # whole file in tool_calls[].input and nothing above touches it. This was
    # THE leak that let the real request stay over the window after compaction.
    _truncate_tool_call_args(messages, max_len=300)

    return messages


def maybe_compact(state, config: dict) -> None:
    """Compact state.messages if approaching context limit."""
    limit = config.get("context_limit", 131072)
    current = estimate_tokens(state.messages)

    if current > limit * 0.60:
        state.messages = compact(state.messages, limit)

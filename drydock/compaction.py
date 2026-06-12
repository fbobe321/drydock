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


def estimate_tokens(messages: list) -> int:
    """Rough token estimate: chars / 3.5"""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content)
        for tc in m.get("tool_calls", []):
            if isinstance(tc, dict):
                for v in tc.values():
                    if isinstance(v, str):
                        total += len(v)
    return int(total / 3.5)


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

    return messages


def maybe_compact(state, config: dict) -> None:
    """Compact state.messages if approaching context limit."""
    limit = config.get("context_limit", 131072)
    current = estimate_tokens(state.messages)

    if current > limit * 0.60:
        state.messages = compact(state.messages, limit)

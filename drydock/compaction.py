"""Context window management — keep conversations within token limits.

DryDock v3 — layered compaction:
1. Truncate long tool results
2. Drop old tool results
3. Emergency mode: aggressive truncation on 400 errors
"""
from __future__ import annotations
import re


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


def extract_server_n_ctx(err: str) -> int | None:
    """Parse the actual server n_ctx from a llama.cpp 400 error message.

    llama.cpp returns: {'n_ctx': 32768, 'n_prompt_tokens': 32830, ...}
    Returns the integer n_ctx, or None if not found.
    """
    m = re.search(r"['\"]?n_ctx['\"]?\s*:\s*(\d+)", err)
    if m:
        return int(m.group(1))
    # Also catch "available context size (32768 tokens)"
    m = re.search(r"available context size \((\d+) token", err)
    if m:
        return int(m.group(1))
    return None


def is_text_only_model_error(err: str) -> bool:
    """Whether a provider 400 says the MODEL can't take images at all (a text-only
    model, e.g. vLLM "X is not a multimodal model", llama.cpp "image input is not
    supported" when no --mmproj is loaded). Distinct from a bad image: the fix is to
    drop attachments and retry, not to ask the user for a different file."""
    e = (err or "").lower()
    return (
        "not a multimodal model" in e
        or "image input is not supported" in e
        or ("does not support" in e and ("image" in e or "multimodal" in e or "vision" in e))
    )


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


def first_divergence_index(before: list, after: list) -> int:
    """Index of the first message that differs — where a re-prefill would start.
    Used to price a compaction's cache cost (cache-aware MCR spec §24)."""
    n = min(len(before), len(after))
    for i in range(n):
        if before[i] != after[i]:
            return i
    return n


def _reclaimable(messages: list, keep_last: int) -> list:
    """Indices of tool results eligible to be shrunk, LATEST FIRST.

    Latest-first is the whole point. Editing message i forces everything from i onward
    to re-prefill, so the cost of reclaiming space falls as the index rises. The
    keep_last window already protects genuinely recent context, so within the eligible
    range the later items are both cheaper to touch and no more valuable than the
    ancient ones the default strategy reaches for first.
    """
    out = [i for i in range(1, max(1, len(messages) - keep_last))
           if messages[i].get("role") == "tool"]
    out.reverse()
    return out


def compact_cache_aware(messages: list, context_limit: int = 131072,
                        target_frac: float = 0.45, keep_last: int = 8) -> list:
    """Compact by reclaiming from the LATEST eligible messages first, so the cached
    prefix survives (cache-aware MCR spec §24, Appendix A.1).

    The default strategy blanket-truncates every long tool result and then drops the
    OLDEST first. Both rewrite the head of the prompt, which measured at ~0.98x a cold
    prefill — two such events accounted for roughly half of all re-prefill work in an
    instrumented run. This variant shrinks the newest reclaimable material until it is
    under target and stops, leaving the early messages byte-identical.

    It trades a little relevance for a lot of cache: the material it touches first is
    mid-age, not recent, because keep_last still protects the tail.
    """
    target = int(context_limit * target_frac)
    if estimate_tokens(messages) <= target:
        return messages

    for i in _reclaimable(messages, keep_last):
        content = messages[i].get("content")
        if isinstance(content, str) and len(content) > 1500:
            head, tail = 400, 300
            messages[i]["content"] = (
                content[:head]
                + f"\n[... {len(content) - head - tail} chars truncated ...]\n"
                + content[-tail:])
            if estimate_tokens(messages) <= target:
                return messages

    for i in _reclaimable(messages, keep_last):
        if messages[i].get("content") != "[tool result removed]":
            messages[i]["content"] = "[tool result removed]"
            if estimate_tokens(messages) <= target:
                return messages

    if estimate_tokens(messages) > target:
        _truncate_tool_call_args(messages, max_len=1500)
    return messages


def compact(messages: list, context_limit: int = 131072,
            target_frac: float = 0.45, force: bool = False) -> list:
    """Compact messages to fit within context limit.

    Strategy:
    1. First pass: truncate long tool results to 800 chars
    2. Second pass: drop oldest tool results if still over limit
    3. Always keep: first user message, last 8 messages

    target_frac is the fraction of the window to compact DOWN to. It must be
    meaningfully below maybe_compact's TRIGGER (0.60): compacting to the same
    0.60 we trigger at frees almost nothing, so context stays pinned just over
    the line and re-compacts every turn while never gaining real headroom — the
    "it doesn't compact when it needs to" symptom. Targeting 0.45 leaves durable
    headroom so a long /loop (history accumulating across iterations) doesn't
    creep back over the wall on the very next turn.
    """
    target = int(context_limit * target_frac)  # durable headroom below the 0.60 trigger

    # Pass 1: Truncate long tool results — but only until we are under target.
    # This used to truncate EVERY long tool result before checking, which overshot
    # badly: on a 25.8k-token history needing 2.6k reclaimed (10%), it destroyed 22.3k
    # (86%), landing at 6.8% of the window while aiming for 45%. The agent lost tool
    # output, file contents and errors it still needed, which on a long task reads as
    # "it forgot what it already learned". Stop at the target the docstring describes.
    # `force` is the manual /compact command: the user asked for space, so do the work
    # even when already under the automatic target. Automatic compaction stops at the
    # target instead of flattening everything (see above).
    if not force and estimate_tokens(messages) <= target:
        return messages
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
                if not force and estimate_tokens(messages) <= target:
                    return messages

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
    """Compact state.messages if approaching context limit.

    Uses the MAX of the char-based estimate and the SERVER's real prompt-token
    count from the last call. estimate_tokens (chars/3.5) undercounts token-dense
    content — code, compiler/build output, tracebacks tokenize to ~3 chars/token
    — so on such tasks the real context (what the gauge shows) can pass 60% while
    the estimate is still under it, and compaction fires late. The real count
    catches that."""
    limit = config.get("context_limit", 131072)
    current = max(estimate_tokens(state.messages), getattr(state, "last_input_tokens", 0))

    if current <= limit * 0.60:
        return

    # MCR §13: when the modular context window is managing residency, global compaction
    # is the FALLBACK, not the default path. Without this, compaction runs first in the
    # agent loop and flattens the transcript to 0.45 of the window — below the
    # assembler's budget — so the modular window sees an already-destroyed history,
    # correctly concludes it has nothing to do, and never engages at all. Paging must
    # get the first attempt; compaction still catches anything paging cannot fit.
    try:
        from drydock.context_window import assemble_for, enabled as modular_enabled
        if modular_enabled(config):
            state.messages = assemble_for(config, state.messages,
                                          system=str(config.get("_system") or ""))
            if max(estimate_tokens(state.messages),
                   getattr(state, "last_input_tokens", 0)) <= limit * 0.60:
                return                      # paging was enough; nothing destroyed
    except Exception:  # noqa: BLE001 — never let paging break the compaction safety net
        pass

    state.messages = compact(state.messages, limit)

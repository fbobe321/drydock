"""Context composition — where do the tokens in a large window actually go?

Written because a probe built on the PRD's premise failed. `docs/cache_aware_mcr_spec.md`
and the MCR PRD both assume "the transcript's bulk is tool results", so the modular
window pages tool results. A live run showed that is false for Drydock: tool output is
already bounded at the tool layer (`_MAX_BASH_OUTPUT_BYTES`, truncated Read/Bash), so by
the time a transcript reaches the provider the big file reads are gone. The window was a
1,829-token system prompt plus many small messages — and paging tool results reclaimed
nothing because there was nothing large left to reclaim.

So before tuning thresholds against that premise, measure the composition of a real
large context and let the data say what is worth paging. Tool-call ARGUMENTS are counted
separately from message content because a Write/Edit carries a whole file body in
`tool_calls[].input`, which is invisible to a naive per-role breakdown (the compactor
already has `_truncate_tool_call_args` for exactly this reason).

Pure/stdlib. Reporting only — nothing here changes what the model sees.
"""
from __future__ import annotations

from drydock.compaction import _count_chars, estimate_tokens


def _tokens(chars: int) -> int:
    return int(chars / 3.0)


def message_tokens(m: dict) -> int:
    """Everything this message costs, including nested tool-call arguments."""
    return estimate_tokens([m])


def tool_call_arg_tokens(m: dict) -> int:
    """Tokens hiding in tool_calls[].input — a Write's file body, an Edit's replacement.
    Counted separately because it is a different thing to page than a tool RESULT."""
    return sum(_tokens(_count_chars(tc)) for tc in (m.get("tool_calls") or []))


def compose(messages: list, system: str = "") -> dict:
    """Token breakdown of a context window. Returns totals by role, the share carried by
    tool-call arguments, the pinned (unpageable) system cost, and the largest individual
    contributors."""
    by_role: dict = {}
    arg_tokens = 0
    items: list = []
    for i, m in enumerate(messages or []):
        role = str(m.get("role", "?"))
        t = message_tokens(m)
        a = tool_call_arg_tokens(m)
        arg_tokens += a
        by_role[role] = by_role.get(role, 0) + t
        content = m.get("content")
        preview = (content[:60] if isinstance(content, str) else f"<{type(content).__name__}>")
        items.append({"index": i, "role": role, "tokens": t, "arg_tokens": a,
                      "preview": preview})
    sys_tokens = _tokens(len(system or ""))
    total = sum(by_role.values()) + sys_tokens
    items.sort(key=lambda d: -d["tokens"])
    return {
        "n_messages": len(messages or []),
        "total_tokens": total,
        "system_tokens": sys_tokens,
        "by_role": by_role,
        "tool_call_arg_tokens": arg_tokens,
        "largest": items[:8],
        # what a tool-result pager could theoretically reclaim, vs what it cannot touch
        "pageable_tool_tokens": by_role.get("tool", 0),
        "unpageable_tokens": total - by_role.get("tool", 0),
    }


def render(profile: dict) -> str:
    """Human-readable breakdown for a status line or a report."""
    total = profile["total_tokens"]
    denom = total or 1        # guard percentages; do not fake the reported total
    lines = [f"context: {total:,} tokens across {profile['n_messages']} messages"]
    lines.append(f"  system (pinned)      {profile['system_tokens']:>8,}  "
                 f"{100 * profile['system_tokens'] / denom:5.1f}%")
    for role, tok in sorted(profile["by_role"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {role:<20} {tok:>8,}  {100 * tok / denom:5.1f}%")
    lines.append(f"  (of which tool-call args {profile['tool_call_arg_tokens']:,})")
    lines.append(f"  pageable as tool results: {profile['pageable_tool_tokens']:,} "
                 f"({100 * profile['pageable_tool_tokens'] / denom:.1f}%)")
    return "\n".join(lines)

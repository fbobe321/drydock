"""Model-specific tuning for Drydock.

Drydock's primary target is a local Gemma-4-26B-A4B served by llama.cpp.
That model needs a handful of accommodations that we learned from real use:

  * its tool-call JSON is corrupted by token streaming, so tool turns must
    be non-streaming;
  * it leaks ``<|channel>…<channel|>`` "thinking" tokens that must be
    stripped before the text is shown or stored;
  * a few interaction-heavy tools send it into loops, so they are gated off;
  * a short, imperative system prompt beats a long capabilities prompt.

These are *behaviours*, expressed here as small pure functions so the agent
loop and provider stay model-agnostic. All logic is original to Drydock.
"""
from __future__ import annotations

import re

# Tools that reliably send small local models into loops or validation
# errors. None of v3's built-ins are in this set yet; the gate exists so
# that adding interaction-heavy tools later doesn't regress Gemma.
GEMMA_DISABLED_TOOLS: frozenset[str] = frozenset({
    "ask_user_question",
    "todo",
    "task_create",
    "task_update",
    "task",
    "invoke_skill",
    "tool_search",
})

# Gemma's leaked thinking-channel block. The closer may be <channel|> OR — when
# a thought runs straight into a (mal)formed tool call — <tool_call|>.
_THINKING_RE = re.compile(r"<\|channel>.*?(?:<channel\|>|<tool_call\|>)", re.DOTALL)
_THINKING_BARE = ("<|channel>", "<channel|>")

# Generic leaked special tokens: <|x|>, <|x>, <x|> (e.g. <|"|>, <|start|>,
# <|endoftext|>). Bounded length and the required pipe make false positives on
# real text/code very unlikely; these are never legitimate model output.
_SPECIAL_TOKEN_RES = (
    re.compile(r"<\|[^>]{0,200}\|>"),
    re.compile(r"<\|[^>]{0,200}>"),
    re.compile(r"<[^>]{0,200}\|>"),
)

# Gemma sometimes emits a tool call as TEXT instead of a structured call —
# `<|tool_call>call:write_file{...}<tool_call|>` — which the API layer can't
# turn into a real call, so nothing runs and the raw blob lands in the chat.
# We detect this, hide the blob, and let the agent loop nudge a clean retry.
_LEAKED_CALL_RE = re.compile(r"<\|tool_call>.*?<tool_call\|>", re.DOTALL)
_LEAKED_CALL_BARE = ("<|tool_call>", "<tool_call|>")


def strip_leaked_tool_calls(text: str) -> tuple[str, bool]:
    """Return (text without text-form tool-call blobs, whether any were found).

    Conservative: only the unambiguous ``<|tool_call>`` markers trigger it, so
    ordinary prose that mentions "tool call" is never touched.
    """
    if not text or "tool_call" not in text:
        return text, False
    found = bool(_LEAKED_CALL_RE.search(text)) or any(
        m in text for m in _LEAKED_CALL_BARE
    )
    if not found:
        return text, False
    cleaned = _LEAKED_CALL_RE.sub("", text)
    for marker in _LEAKED_CALL_BARE:
        cleaned = cleaned.replace(marker, "")
    return cleaned.strip(), True

_DEFAULT_SYSTEM_PROMPT = (
    "You are Drydock, a coding agent operating in a terminal. You have tools "
    "to read, write, edit, and search files and to run shell commands. Work "
    "directly: inspect what you need, make the change, and verify it. Prefer "
    "doing over explaining. When the task is complete, stop and give a short "
    "summary."
)

# Short, imperative prompt. Small local models do better with "act now" than
# with a long capabilities essay.
_GEMMA_SYSTEM_PROMPT = (
    "You are Drydock, a coding agent in a terminal. ACT IMMEDIATELY using "
    "tools — do not narrate what you are about to do. Read a file before you "
    "edit it. Make the edit with the Edit or Write tool. Run a command to "
    "check your work. One clear action per step. Stop when the task is done "
    "and give a one-line summary."
)


def is_gemma(model: str | None) -> bool:
    """True if the model name looks like a Gemma model."""
    return bool(model) and "gemma" in model.lower()


def strip_thinking_tokens(text: str) -> str:
    """Remove Gemma's leaked channel/thinking markers and stray special tokens.

    Handles the <|channel>…<channel|> (or …<tool_call|>) block, stray bare
    channel markers, and generic leaked <|…|> / <|…> / <…|> special tokens.
    Safe to call on any text (no-op when no markers are present). NOTE: call
    AFTER strip_leaked_tool_calls so the <|tool_call> blocks are consumed first
    (otherwise the generic pass would eat their markers and break leak recovery).
    """
    if not text or ("<|" not in text and "|>" not in text):
        return text
    text = _THINKING_RE.sub("", text)
    for marker in _THINKING_BARE:
        text = text.replace(marker, "")
    for rx in _SPECIAL_TOKEN_RES:
        text = rx.sub("", text)
    return text


# Tool names small models hallucinate (from training on other agents' prompts).
# Returning "tool not found" makes them loop; instead we hand back a benign,
# directive result that points at the real tools. Ported from v2's
# hallucinated-tool dropping.
_HALLUCINATED_TOOLS: dict[str, str] = {
    "exit_plan_mode": "Drydock has no plan mode. Proceed directly with the real tools.",
    "enter_plan_mode": "Drydock has no plan mode. Proceed directly with the real tools.",
    "plan": "Drydock has no plan mode. Proceed directly with the real tools.",
    "lsp": "No LSP tool. To find code use Grep; to read a file use Read.",
    "ralph_repo_index": "No such tool. Use Glob to list files and Grep to search them.",
    "read_mcp_resource": "No MCP resources here. Use Read for files, Grep/Glob to search.",
    "list_mcp_resources": "No MCP resources here. Use Glob to list files.",
    "todo": "No todo tool. Just do the work with the real tools.",
    "task": "No task tool. Do the work directly with Read/Write/Edit/Bash.",
}


def hallucinated_tool_message(name: str) -> str | None:
    """A benign redirect for a hallucinated tool name, or None if `name` is a
    real tool the model should actually be allowed to call."""
    return _HALLUCINATED_TOOLS.get(name)


def use_streaming(model: str | None, has_tools: bool) -> bool:
    """Whether to stream tokens for this turn.

    Gemma corrupts tool-call JSON when streamed, so any turn that offers
    tools to Gemma must be non-streaming. Text-only turns may stream.
    """
    if has_tools and is_gemma(model):
        return False
    return True


def filter_tool_schemas(tool_schemas: list, model: str | None) -> list:
    """Drop loop-prone tools when serving Gemma; pass through otherwise."""
    if not is_gemma(model):
        return tool_schemas
    return [t for t in tool_schemas if t.get("name") not in GEMMA_DISABLED_TOOLS]


def system_prompt_for_model(model: str | None) -> str:
    """Return the system prompt best suited to the model."""
    return _GEMMA_SYSTEM_PROMPT if is_gemma(model) else _DEFAULT_SYSTEM_PROMPT


def thinking_level_for_turn(turn_count: int, is_user_turn: bool) -> str:
    """Adaptive reasoning budget: HIGH for planning, LOW for recovery, OFF
    for routine continuation.

    Returns one of "high" | "low" | "off". The provider maps the level to a
    concrete request parameter (or ignores it for endpoints without a
    reasoning knob). Keeping it a label here keeps the policy in one place.
    """
    if is_user_turn or turn_count <= 1:
        return "high"
    return "off"

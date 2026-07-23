"""Ghost-text suggestion for the prompt box — the dimmed reply drydock proposes
after each turn (à la Claude Code). Tab accepts it. Two sources, in order:

  1. suggest_reply_llm() — a SHORT, bounded model call that reads the agent's last
     message and proposes the user's likely next reply (answer its question, or
     "continue"). This is what makes the suggestion actually good.
  2. suggest_next_command() — a zero-cost heuristic fallback used when the LLM
     suggestion is disabled, fails, or the endpoint is busy/offline.

Pure logic (no Textual imports) so it's unit-testable; the TUI renders the result
into the PromptArea's placeholder.
"""
from __future__ import annotations

import re

# strip Gemma-4 thinking-channel leakage (same family the agent history filters)
_THINK = re.compile(r"<\|?channel\|?>.*?(<\|?/?channel\|?>|$)", re.DOTALL)


def _clean(text: str) -> str:
    text = _THINK.sub("", text or "")
    text = text.strip().strip('"').strip()
    # first non-empty line only; ghost text is one line
    for line in text.splitlines():
        line = line.strip().strip('"').strip()
        if line:
            return line[:100]
    return ""


def suggest_next_command(
    *,
    ctx_pct: int,
    wrote_files: bool,
    ran_bash: bool,
    had_error: bool,
    in_git: bool,
    plan_remaining: bool,
    asked_question: bool = False,
) -> str | None:
    """Heuristic fallback: the single most useful next step given what the last
    turn did, or None to show nothing (better no hint than a noisy one)."""
    if ctx_pct >= 78:
        return "/compact"          # context nearly full — shrink it before continuing
    if asked_question:
        return "yes, go ahead"     # the agent ended by asking — a sane default reply
    if had_error:
        return "fix the error above"
    if plan_remaining:
        return "continue"          # the model left plan steps unfinished
    if wrote_files and in_git:
        return "review the changes with git diff"
    if wrote_files:
        return "run the tests to verify"
    if ran_bash:
        return None                # ran a command but nothing else obvious — no clutter
    return None


def ends_with_question(text: str) -> bool:
    """True if the agent's last message reads like it is asking the user something."""
    t = _clean(text)
    if not t:
        return False
    if t.rstrip().endswith("?"):
        return True
    low = t.lower()
    return any(low.startswith(p) for p in (
        "should i", "shall i", "do you want", "would you like", "want me to",
        "which ", "how would you", "let me know",
    ))


def build_suggest_prompt(last_assistant: str) -> tuple[str, str]:
    """(system, user) for the bounded suggestion call. Kept here so it's testable."""
    system = (
        "You propose the USER'S most likely next reply to a terminal coding agent. "
        "Read the agent's last message and output ONE short line (<= 12 words) the user "
        "would plausibly type next: answer its question directly, or say 'continue' if it "
        "paused mid-task. Output ONLY that reply — no quotes, no preamble, no explanation. "
        "If nothing useful fits, output the single word NONE."
    )
    user = f"The agent's last message:\n\n{(last_assistant or '')[-2000:]}\n\nUser's likely reply:"
    return system, user


def suggest_reply_llm(last_assistant: str, config: dict) -> str | None:
    """A short, bounded model call → the user's likely next reply, or None. Never
    raises; returns None on any failure so the caller falls back to the heuristic.
    Bounded (≤32 tokens, last message only, 12s timeout) so it stays cheap on a
    local GPU."""
    if not (last_assistant or "").strip():
        return None
    try:
        import os

        from openai import OpenAI

        from drydock.providers import PROVIDERS

        prov = PROVIDERS.get(config.get("provider", "vllm"), PROVIDERS["vllm"])
        base_url = config.get("base_url") or prov.get("base_url", "http://localhost:8000/v1")
        api_key = (config.get("api_key") or prov.get("api_key")
                   or os.environ.get(prov.get("api_key_env", ""), "dummy"))
        model = config.get("model", "gemma4")
        system, user = build_suggest_prompt(last_assistant)
        client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=15.0)
        # disable the model's thinking phase — otherwise a reasoning-budget model
        # (Gemma 4 w/ llama.cpp --reasoning-budget) spends the whole token cap on
        # thinking and returns empty. enable_thinking:false → a direct short reply
        # in ~1s. Harmless to endpoints that ignore the kwarg.
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_tokens=48, temperature=0.3, stream=False,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        txt = _clean(r.choices[0].message.content or "")
        if not txt or txt.strip().upper() == "NONE":
            return None
        return txt
    except Exception:  # noqa: BLE001 — a suggestion must never break the TUI
        return None

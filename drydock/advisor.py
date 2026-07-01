"""Second-model "advisor" — consult a stronger model for a second opinion.

Drydock's primary model is a small local one. Sometimes you want a heavier model
(e.g. Gemini) to sanity-check a design, unstick a hard bug, or review an
approach. The advisor is just ANOTHER OpenAI-compatible endpoint — Gemini exposes
one (`.../v1beta/openai/`), and any proxy on another box works too — so this
reuses the existing `openai` client with no new dependency.

It's opt-in and user-configured (empty by default): the only network call is the
one YOU point it at, consistent with drydock's no-phone-home stance. Exposed as
the `Consult` tool (the agent asks when stuck) and the `/ask` command (you ask).

All logic original to Drydock.
"""
from __future__ import annotations

_ADVISOR_SYSTEM = (
    "You are a senior engineering advisor consulted by another AI coding agent "
    "(Drydock) running a smaller local model. Give concise, direct, expert advice "
    "— the specific fix, the design trade-off, or the likely root cause. Prefer "
    "concrete steps and short code over prose. If the question is under-specified, "
    "state the key assumption and answer anyway. You cannot run tools; the calling "
    "agent will act on your answer."
)


def is_configured(config: dict) -> bool:
    """True when an advisor endpoint + model are set."""
    return bool((config.get("advisor_base_url") or "").strip()
                and (config.get("advisor_model") or "").strip())


def not_configured_message() -> str:
    return (
        "No advisor (second) model is configured. Point it at any OpenAI-compatible "
        "endpoint — e.g. Gemini's, or a proxy on another box:\n"
        "  /advisor url  http://<other-box>:<port>/v1\n"
        "  /advisor model  gemini-2.5-pro\n"
        "  /advisor key  <api-key>            (if the endpoint needs one)\n"
        "Then use  /ask <question>  or let the agent call the Consult tool."
    )


def _call(question: str, config: dict, *, context: str = "",
          timeout: float = 120.0, max_tokens: int = 2048) -> str:
    """Make one advisor call. RAISES on transport/model error (callers decide how
    to surface it)."""
    import httpx
    from openai import OpenAI

    base = config["advisor_base_url"].strip()
    model = config["advisor_model"].strip()
    key = (config.get("advisor_api_key") or "").strip() or "dummy"
    messages: list[dict] = [{"role": "system", "content": _ADVISOR_SYSTEM}]
    if context.strip():
        messages.append({"role": "user",
                         "content": f"Context from the current task:\n{context.strip()}"})
    messages.append({"role": "user", "content": question})
    kwargs = {"model": model, "messages": messages, "temperature": 0.3, "max_tokens": max_tokens}
    client = OpenAI(api_key=key, base_url=base, max_retries=1,
                    timeout=httpx.Timeout(timeout, connect=10.0))
    resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


def consult(question: str, config: dict, *, context: str = "", timeout: float = 120.0) -> str:
    """Ask the configured advisor model and return its answer (or a clean error).
    Never raises — a failed consult must not crash the turn."""
    question = (question or "").strip()
    if not question:
        return "Error: nothing to ask the advisor."
    if not is_configured(config):
        return not_configured_message()
    try:
        return _call(question, config, context=context, timeout=timeout) \
            or "(the advisor model returned an empty response)"
    except Exception as e:  # noqa: BLE001 — never crash the caller
        return (f"Could not reach the advisor model ({config.get('advisor_model')}) at "
                f"{config.get('advisor_base_url')}: {e}\n"
                "Check /advisor settings and that the endpoint is up.")


def test_connection(config: dict) -> str:
    """Ping the advisor endpoint with a trivial prompt and report reachability +
    latency — one-shot setup confirmation for `/advisor test`."""
    import time

    if not is_configured(config):
        return not_configured_message()
    model = config.get("advisor_model")
    base = config.get("advisor_base_url")
    t0 = time.monotonic()
    try:
        # Generous read timeout: a slow/cold LOCAL model can take a while to
        # return even a few tokens. A truly-down endpoint fails fast on the 10s
        # connect timeout, so this only waits on a reachable-but-slow server.
        reply = _call("Reply with exactly: OK", config, timeout=90.0, max_tokens=8)
    except Exception as e:  # noqa: BLE001
        emsg = str(e).lower()
        if "timeout" in emsg or "timed out" in emsg:
            return (f"✗ advisor timed out (>90s) — {model} at {base} is REACHABLE but slow "
                    "to respond (a cold or loaded local model?). `/ask` allows much longer, "
                    "so it may still work; or give the server a moment and retry.")
        return (f"✗ advisor unreachable — {model} at {base}\n   {e}\n"
                "   Check the endpoint is up, the URL ends in /v1, and the key/model are right.")
    dt = time.monotonic() - t0
    return (f"✓ advisor reachable — {model} at {base} responded in {dt:.1f}s"
            + (f' (said: "{reply[:40]}").' if reply else "."))

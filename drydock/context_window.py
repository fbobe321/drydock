"""Modular context window — assemble the prompt from addressable modules instead of
replaying an append-only transcript.

This is the part of the Modular Context Runtime that actually touches the context
window. Everything else in MCR is a store sitting beside the conversation; this decides
what the model sees.

The transcript's bulk is tool results — file reads, test output, command output. Here
each one becomes an addressable module with several resolutions:

    L2  full text          (what the transcript holds today)
    L1  head + tail        (enough to recall what it was)
    L0  pointer            (it existed, it is retrievable, it costs ~20 tokens)

Assembly then chooses a resolution per module to fit a budget, instead of either
replaying everything or destructively scribbling out the history. The difference from
compaction is that nothing is lost: the full text stays in the store and a later turn can
page a module back up.

Two rules make this safe to do every turn:

  * Resolutions are MONOTONE — a module only ever moves down, never back up
    automatically. Otherwise the prompt would churn turn to turn and the server's cached
    prefix would be invalidated constantly (Appendix A.1: a head edit costs ~0.98x a cold
    prefill). Monotone downgrades mean a prompt that is stable once settled.
  * Downgrades are applied LATEST-FIRST, so the earliest messages stay byte-identical and
    the cached prefix survives.

The recent window (`keep_last`) is never touched at any resolution.
"""
from __future__ import annotations

import os
import threading

from drydock.compaction import estimate_tokens
from drydock.context_runtime import ContextModule, ContextStore

# resolution levels, most detailed first
L_FULL, L_SUMMARY, L_POINTER = 2, 1, 0
LEVELS = (L_FULL, L_SUMMARY, L_POINTER)

MIN_MODULARIZE_CHARS = 1500     # below this a tool result is not worth addressing


def tool_module_id(index: int) -> str:
    """Stable address for the tool result at message `index`. Message indices are stable
    because the transcript only ever appends."""
    return f"ctx://tool/{index}"


def _summary(content: str, head: int = 400, tail: int = 300) -> str:
    if len(content) <= head + tail:
        return content
    return (content[:head]
            + f"\n[... {len(content) - head - tail} chars paged out ...]\n"
            + content[-tail:])


def _pointer(cid: str, content: str) -> str:
    return f"[{cid} — {len(content)} chars, paged out of context; still stored]"


def modularize(messages: list, store: ContextStore) -> dict:
    """Register each substantial tool result as an addressable module. Returns
    {message_index: context_id}. Idempotent: re-registering an unchanged module is a
    no-op, so this can run every turn."""
    out: dict = {}
    for i, m in enumerate(messages):
        if m.get("role") != "tool":
            continue
        content = m.get("content")
        if not isinstance(content, str) or len(content) < MIN_MODULARIZE_CHARS:
            continue
        cid = tool_module_id(i)
        out[i] = cid
        existing = store.get(cid)
        if existing is not None and existing.resolutions.get(str(L_FULL)) == content:
            continue                                   # unchanged — do not churn versions
        store.put(ContextModule(
            context_id=cid, type="tool_result", residency="working", scope="branch",
            resolutions={L_FULL: content,
                         L_SUMMARY: _summary(content),
                         L_POINTER: _pointer(cid, content)},
            level=(existing.level if existing is not None else L_FULL),
        ))
    return out


def assemble(messages: list, store: ContextStore, budget: int,
             keep_last: int = 8) -> "tuple[list, dict]":
    """Build the outgoing message list from modules under a token budget.

    Returns (messages, report). Never mutates the caller's list. When the transcript
    already fits, it is returned unchanged — assembly must cost nothing in the common
    case."""
    report = {"budget": budget, "downgraded": {}, "before_tokens": estimate_tokens(messages),
              "after_tokens": 0, "first_changed_index": None}
    if report["before_tokens"] <= budget:
        report["after_tokens"] = report["before_tokens"]
        return list(messages), report

    idx_to_cid = modularize(messages, store)
    out = [dict(m) for m in messages]

    # apply resolutions already settled in the store (monotone: never upgrade)
    for i, cid in idx_to_cid.items():
        mod = store.get(cid)
        if mod is not None and mod.level is not None and mod.level != L_FULL:
            out[i]["content"] = mod.text_at(mod.level)
            report["downgraded"][cid] = mod.level

    eligible = [i for i in sorted(idx_to_cid, reverse=True)
                if i < len(messages) - keep_last]

    # then degrade further, latest-first, one level at a time, until it fits
    for level in (L_SUMMARY, L_POINTER):
        if estimate_tokens(out) <= budget:
            break
        for i in eligible:
            if estimate_tokens(out) <= budget:
                break
            cid = idx_to_cid[i]
            mod = store.get(cid)
            if mod is None or (mod.level is not None and mod.level <= level):
                continue
            out[i]["content"] = mod.text_at(level)
            mod.level = level
            store.put(mod)                              # settled: stays down (monotone)
            report["downgraded"][cid] = level

    report["after_tokens"] = estimate_tokens(out)
    for i, (a, b) in enumerate(zip(messages, out)):
        if a != b:
            report["first_changed_index"] = i
            break
    return out, report


# ── integration ──────────────────────────────────────────────────────────────

_STORES: "dict[str, ContextStore]" = {}
_LOCK = threading.Lock()

DEFAULT_BUDGET_FRAC = 0.55   # of context_limit; below compaction's 0.60 trigger


def enabled(config: dict | None) -> bool:
    """Separate switch from the MCR packet: this one decides what the model sees, so it
    should be possible to turn the risky half off on its own."""
    cfg = config or {}
    return bool(cfg.get("modular_context") or os.environ.get("DRYDOCK_MODULAR_CONTEXT"))


def store_for(cwd: str) -> ContextStore:
    with _LOCK:
        s = _STORES.get(cwd)
        if s is None:
            s = ContextStore(root=cwd, name="window")
            _STORES[cwd] = s
        return s


def reset_stores() -> None:
    with _LOCK:
        _STORES.clear()


def assemble_for(config: dict | None, messages: list) -> list:
    """Hook for the provider: assemble the window from modules. Returns `messages`
    unchanged when disabled or already within budget. Never raises — a failure here
    must fall back to the ordinary transcript rather than breaking the run."""
    if not enabled(config):
        return messages
    try:
        cfg = config or {}
        cwd = str(cfg.get("cwd") or ".")
        limit = int(cfg.get("context_limit") or 131072)
        budget = int(limit * float(cfg.get("modular_context_frac") or DEFAULT_BUDGET_FRAC))
        out, report = assemble(messages, store_for(cwd), budget=budget)
        cfg.setdefault("_mcr_window", {})["last_report"] = report
        return out
    except Exception:  # noqa: BLE001
        return messages

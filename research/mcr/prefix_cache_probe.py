"""Prefix-cache probe — does evicting a context module from the prompt HEAD destroy
the server's KV prefix cache?

This is the decisive de-risking measurement for the Modular Context Runtime
(docs/modular_context_runtime_prd.md Appendix A.1). MCR's premise is "lossless
storage, selective residency": mount/unmount modules between inference calls to keep
a small working set. But vLLM/llama.cpp automatic prefix caching only reuses a
*prefix*. Mutating an EARLY module invalidates everything after it and forces a full
re-prefill — so a naive pager can be slower and more expensive than the append-only
baseline it is trying to beat, while truthfully reporting a smaller resident context.

What this measures, per scenario: prompt tokens, server-reported CACHED prompt tokens
(OpenAI `usage.prompt_tokens_details.cached_tokens`), cache-hit %, and latency at
max_tokens=1 (≈ prefill cost).

  cold          first send of the base prompt            → expect ~0% cached
  identical     resend, unchanged                        → expect ~100% cached
  tail_mutate   change only the LAST module              → expect ~high% cached
  middle_mutate change a middle module                   → expect cached up to it
  head_mutate   change the FIRST module (an eviction)    → expect ~0% cached

If head_mutate ≈ cold while tail_mutate ≈ identical, the MCR Context View MUST be
ordered pinned → shared → task → volatile, so residency changes only ever mutate the
TAIL of the prompt. That is the design rule this probe exists to confirm or refute.

NOT an eval harness: this measures inference-server cache behaviour, not model quality.

Usage:
    python research/mcr/prefix_cache_probe.py --base-url http://192.168.50.21:8000/v1 \
        --model nemotron [--modules 8] [--module-tokens 900] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request


def _module_text(idx: int, approx_tokens: int, salt: str = "a") -> str:
    """A deterministic, incompressible-ish block standing in for a context module.
    ~3 chars/token (matching drydock.compaction's estimator)."""
    head = f"### ctx://probe/module-{idx} (salt={salt})\n"
    body_chars = max(0, approx_tokens * 3 - len(head))
    # varied tokens so the model can't trivially collapse it; deterministic per (idx, salt)
    words = [f"{salt}{idx}w{i}" for i in range(body_chars // 8 + 1)]
    return head + " ".join(words)[:body_chars] + "\n"


def build_prompt(modules: list) -> str:
    return "\n".join(modules) + "\n\n### QUERY\nReply with the single word: ok\n"


def post_completion(base_url: str, model: str, prompt: str, timeout: float = 600.0) -> dict:
    """One max_tokens=1 chat completion; returns {latency, usage}."""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1,
        "temperature": 0.0,
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — operator's own server
        body = json.loads(r.read().decode("utf-8"))
    return {"latency": time.time() - t0, "usage": body.get("usage", {}) or {}}


def cached_tokens(usage: dict) -> "int | None":
    """vLLM reports reused prefix tokens as usage.prompt_tokens_details.cached_tokens."""
    d = usage.get("prompt_tokens_details") or {}
    v = d.get("cached_tokens")
    return int(v) if isinstance(v, int) else None


def _measure(base_url: str, model: str, mods: list, label: str) -> dict:
    res = post_completion(base_url, model, build_prompt(mods))
    u = res["usage"]
    pt = int(u.get("prompt_tokens") or 0)
    ct = cached_tokens(u)
    row = {
        "scenario": label,
        "prompt_tokens": pt,
        "cached_tokens": ct,
        "cache_hit_pct": (round(100.0 * ct / pt, 1) if (ct is not None and pt) else None),
        "latency_s": round(res["latency"], 3),
    }
    print(f"  {label:22s} prompt={pt:6d} cached={ct if ct is not None else 'n/a':>6} "
          f"hit={row['cache_hit_pct']}%  {row['latency_s']}s", flush=True)
    return row


def run(base_url: str, model: str, n_modules: int, module_tokens: int,
        salt: str = "a") -> list:
    """Each mutation is measured as prime → prime_check → mutate.

    The prime_check (re-sending the identical base) is load-bearing: on a CONTENDED
    server the KV blocks for our prefix get evicted between requests, which looks
    exactly like prefix invalidation. If prime_check is not ~100% cached the box is
    too noisy and that scenario's reading is meaningless — so we mark it invalid
    rather than draw a conclusion from it. (Observed on a contended .20: an identical
    re-send returned 0% cached.)
    """
    # `salt` makes the base prompt unique per run, so "cold" is genuinely cold —
    # a re-run with the same text would hit the cache left by the previous run.
    base = [_module_text(i, module_tokens, salt=salt) for i in range(n_modules)]

    def mutate(pos: int) -> list:
        out = list(base)
        out[pos] = _module_text(pos, module_tokens, salt=salt + "Z")  # same size, new content
        return out

    cold = _measure(base_url, model, base, "cold")
    # The COLD run is the only genuinely uncached full prefill, so it is the baseline
    # for every ratio below. (Comparing a cached `prime` against a cached `prime_check`
    # gives ~1.0 and looks like "no reuse" even when caching is working perfectly.)
    cold_lat = cold["latency_s"] or 0.0
    rows = [cold]
    for name, pos in (("tail_mutate", n_modules - 1),
                      ("middle_mutate", n_modules // 2),
                      ("head_mutate", 0)):
        _measure(base_url, model, base, f"{name}:prime")
        check = _measure(base_url, model, base, f"{name}:prime_check")
        test = _measure(base_url, model, mutate(pos), name)
        hit = check.get("cache_hit_pct")
        # Two independent reuse signals. cached_tokens is exact but not every build
        # reports it (v0.26.0 does not); latency is always available and is what we
        # actually care about — re-prefill cost.
        ratio = (check["latency_s"] / cold_lat) if cold_lat else 1.0
        by_tokens = hit is not None and hit >= 90
        by_latency = ratio <= 0.5            # identical resend far cheaper than a cold prefill
        test["prime_check_pct"] = hit
        test["cold_latency_s"] = cold_lat
        test["prime_check_ratio"] = round(ratio, 3)
        test["valid"] = bool(by_tokens or by_latency)
        if not test["valid"]:
            print(f"    ⚠ {name}: identical resend was NOT cheaper than a cold prefill "
                  f"(hit={hit}%, latency ratio={ratio:.2f}) — no prefix reuse detected; "
                  "reading INVALID (caching off, or blocks evicted by contention)", flush=True)
        else:
            print(f"    · {name}: re-prefill cost {test['latency_s'] / cold_lat:.2f}× a cold "
                  f"prefill (identical resend was {ratio:.2f}×)", flush=True)
        rows.extend([check, test])
    return rows


def _latency_verdict(head: dict, tail: dict, mid: "dict | None" = None) -> str:
    """Fallback when cached_tokens is unreported: compare re-prefill COST directly,
    against the cold (genuinely uncached) prefill as the 1.0 baseline."""
    full = tail.get("cold_latency_s") or head.get("cold_latency_s") or 0.0
    if not full:
        return "INCONCLUSIVE — no baseline latency captured."
    mid_txt = ""
    if mid and mid.get("latency_s"):
        mid_txt = f" middle={mid['latency_s'] / full:.2f}×,"
    t = tail["latency_s"] / full
    h = head["latency_s"] / full
    if h - t >= 0.25:
        return (f"CONFIRMED by latency (Appendix A.1): re-prefill cost scales with how EARLY the "
                f"mutation is — tail={t:.2f}×,{mid_txt} head={h:.2f}× of a cold prefill "
                f"(head costs {h / t:.1f}× a tail mutation). MCR's Context View MUST be ordered "
                "pinned → shared → task → volatile so residency changes only ever mutate the "
                "TAIL; and CTE must count re-prefill tokens or it will flatter the pager.")
    return (f"NOT CONFIRMED by latency — tail={t:.2f}× vs head={h:.2f}× of a cold prefill; "
            "head and tail mutation cost about the same, so prefix position does not matter "
            "on this engine/config. Re-check before designing around it.")


def verdict(rows: list) -> str:
    by = {r["scenario"]: r for r in rows}
    head, tail = by.get("head_mutate"), by.get("tail_mutate")
    if not (head and tail):
        return "INCONCLUSIVE — scenarios missing."
    if not (head.get("valid") and tail.get("valid")):
        return ("INVALID — no prefix reuse detected even for an IDENTICAL resend "
                f"(tail prime_check ratio={tail.get('prime_check_ratio')}, "
                f"head={head.get('prime_check_ratio')}). Either prefix caching is disabled on "
                "this server (check `enable_prefix_caching` in the engine config) or the blocks "
                "are being evicted by contention. Fix that, then re-run.")
    if head.get("cache_hit_pct") is None:
        return _latency_verdict(head, tail, by.get("middle_mutate"))
    if tail["cache_hit_pct"] - head["cache_hit_pct"] >= 25:
        return ("CONFIRMED (Appendix A.1): mutating the prompt HEAD destroys the prefix cache "
                f"({head['cache_hit_pct']}% hit) while mutating the TAIL preserves it "
                f"({tail['cache_hit_pct']}% hit). MCR's Context View MUST be ordered "
                "pinned → shared → task → volatile so residency changes only mutate the tail, "
                "and CTE must count re-prefill tokens.")
    return ("NOT CONFIRMED — head and tail mutation cost about the same "
            f"({head['cache_hit_pct']}% vs {tail['cache_hit_pct']}%). Either prefix caching is "
            "off or the engine handles this differently; re-check before designing around it.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base-url", default="http://192.168.50.21:8000/v1")
    ap.add_argument("--model", default="nemotron")
    ap.add_argument("--modules", type=int, default=8)
    ap.add_argument("--module-tokens", type=int, default=900)
    ap.add_argument("--salt", default="", help="run id; default = timestamp (keeps 'cold' cold)")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    print(f"prefix-cache probe → {a.base_url} model={a.model} "
          f"({a.modules} modules × ~{a.module_tokens} tok)")
    try:
        rows = run(a.base_url, a.model, a.modules, a.module_tokens,
                   salt=a.salt or f"s{int(time.time())}")
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"ERROR: could not reach the server: {e}")
        return 1
    v = verdict(rows)
    print("\nVERDICT:", v)
    if a.json:
        try:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump({"rows": rows, "verdict": v}, f, indent=2)
            print(f"wrote {a.json}")
        except OSError as e:
            print(f"(could not write {a.json}: {e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

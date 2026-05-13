#!/usr/bin/env python3
"""Benchmark llama.cpp inference speed on the local server.

Used for MTP (speculative decoding) before/after comparisons. Hits the
running llama.cpp server at localhost:8000 with a fixed set of prompts,
measures total wall-clock and tokens/sec.

Usage:
    python3 scripts/bench_inference.py                 # quick (5 prompts, 200 tok)
    python3 scripts/bench_inference.py --tokens 500    # longer outputs
    python3 scripts/bench_inference.py --tag baseline  # label the run
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request

ENDPOINT = "http://localhost:8000/v1/chat/completions"

PROMPTS: list[tuple[str, str]] = [
    ("code-write", "Write a Python function that computes the SHA-256 of a file in 256KB chunks. Just the function, no markdown."),
    ("code-explain", "Explain in 4 sentences what speculative decoding does in LLM inference."),
    ("math-step", "Compute 17! step by step and give the final number."),
    ("structured", "List five Python standard library modules useful for parsing structured data, with one-line descriptions each."),
    ("conversational", "Suggest a healthy breakfast for someone training for a marathon. Two sentences."),
]


def hit(prompt: str, max_tokens: int) -> tuple[float, int]:
    """POST one prompt, return (elapsed_sec, output_tokens)."""
    payload = json.dumps({
        "model": "gemma4",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }).encode()
    req = urllib.request.Request(
        ENDPOINT, data=payload, headers={"Content-Type": "application/json"}
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
    elapsed = time.perf_counter() - t0
    out_tokens = data.get("usage", {}).get("completion_tokens", 0)
    return elapsed, out_tokens


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", type=int, default=200,
                    help="max_tokens per prompt (default 200)")
    ap.add_argument("--tag", default="run", help="label printed with results")
    args = ap.parse_args()

    print(f"[{args.tag}] benchmarking {len(PROMPTS)} prompts × {args.tokens} tokens")
    print(f"[{args.tag}] endpoint: {ENDPOINT}")
    print()

    total_elapsed = 0.0
    total_tokens = 0
    rows = []
    for label, prompt in PROMPTS:
        elapsed, tokens = hit(prompt, args.tokens)
        total_elapsed += elapsed
        total_tokens += tokens
        tps = tokens / elapsed if elapsed > 0 else 0.0
        rows.append((label, tokens, elapsed, tps))
        print(f"  {label:<16s} {tokens:>4d} tok in {elapsed:>6.2f}s  =  {tps:>5.1f} tok/s")

    overall_tps = total_tokens / total_elapsed if total_elapsed > 0 else 0.0
    print()
    print(f"[{args.tag}] TOTAL: {total_tokens} tokens in {total_elapsed:.2f}s  =  {overall_tps:.1f} tok/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

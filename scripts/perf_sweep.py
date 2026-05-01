#!/usr/bin/env python3
"""
perf_sweep.py — Gemma 4 / vLLM hosting performance harness.

Measures TTFT, decode tok/s, total time across prompt sizes that match
real Drydock workloads. Two modes:

  baseline    Runs against the current live vLLM. Read-only, does not
              restart anything. Safe to run while stress harness is active
              (it will just contend for GPU briefly).

  sweep       Restarts vLLM between configs to test attention backend,
              KV cache dtype, prefix caching, max-num-seqs. DESTRUCTIVE
              to any active session — only run when stress harness is
              idle.

Both modes write JSON results to /data3/drydock/perf_results/<timestamp>.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

try:
    from openai import OpenAI
except ImportError:
    print("openai package required: pip install openai", file=sys.stderr)
    sys.exit(1)


RESULTS_DIR = Path("/data3/drydock/perf_results")
RESULTS_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Workload definitions — sized to mirror real Drydock turns
# ---------------------------------------------------------------------------

def _filler(token_count: int) -> str:
    """Approximate-token-count filler. Gemma tokenizer averages ~4 chars/token."""
    chunk = (
        "The quick brown fox jumps over the lazy dog. "
        "Pack my box with five dozen liquor jugs. "
        "How razorback-jumping frogs can level six piqued gymnasts. "
    )
    target_chars = token_count * 4
    out = (chunk * (target_chars // len(chunk) + 1))[:target_chars]
    return out


WORKLOADS = {
    "short": {
        "desc": "~50 input tokens — chat-style",
        "prompt": "Write a one-sentence summary of TCP congestion control.",
        "max_tokens": 64,
    },
    "medium": {
        "desc": "~2K input tokens — typical drydock turn (system + few tool results)",
        "prompt": (
            "You are reviewing the following log excerpt. Identify the most "
            "significant anomaly and explain it in three sentences.\n\n"
            + _filler(1900)
            + "\n\nReport now."
        ),
        "max_tokens": 256,
    },
    "long": {
        "desc": "~16K input tokens — drydock with substantial tool history",
        "prompt": (
            "Review the following accumulated tool output. Summarize what "
            "the agent has learned in four sentences.\n\n"
            + _filler(15800)
            + "\n\nSummarize."
        ),
        "max_tokens": 256,
    },
    "xlong": {
        "desc": "~64K input tokens — heavy context, near-half max",
        "prompt": (
            "Below is an extended session log. Identify any repeated tool "
            "call patterns in three sentences.\n\n"
            + _filler(63800)
            + "\n\nReport patterns."
        ),
        "max_tokens": 256,
    },
}


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    workload: str
    iter: int
    ttft_s: float
    total_s: float
    output_tokens: int
    e2e_tok_s: float        # output_tokens / total_s — user-visible
    decode_tok_s: float     # only meaningful if server streams; 0 if buffered
    streamed: bool          # true if >1 content chunk
    chunk_count: int
    error: str | None = None


def run_one(client: OpenAI, model: str, prompt: str, max_tokens: int) -> RunResult:
    """
    Measures TTFT, end-to-end throughput, and (when meaningful) decode rate.

    vLLM with --tool-call-parser gemma4 buffers content server-side so all
    chunks may arrive within a few ms at the end. In that case decode_tok_s
    is meaningless — e2e_tok_s (output / wall-clock) is the right number.
    """
    t0 = time.perf_counter()
    ttft = None
    first_content_time = None
    last_content_time = None
    server_completion_tokens = None
    chunk_count = 0
    err = None
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.2,
            stream=True,
            stream_options={"include_usage": True},
        )
        for chunk in stream:
            if chunk.usage is not None:
                server_completion_tokens = chunk.usage.completion_tokens
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                now = time.perf_counter()
                if ttft is None:
                    ttft = now - t0
                    first_content_time = now
                last_content_time = now
                chunk_count += 1
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    total = time.perf_counter() - t0
    out_tokens = server_completion_tokens or 0

    e2e_tok_s = (out_tokens / total) if total > 0 and out_tokens > 0 else 0.0

    # decode_tok_s only meaningful if server actually streamed multiple
    # content chunks spread over time (>50ms span)
    decode_tok_s = 0.0
    streamed = False
    if (
        first_content_time is not None
        and last_content_time is not None
        and out_tokens > 1
    ):
        decode_span = last_content_time - first_content_time
        if decode_span > 0.05:
            streamed = True
            decode_tok_s = (out_tokens - 1) / decode_span

    return RunResult(
        workload="",
        iter=0,
        ttft_s=ttft or 0.0,
        total_s=total,
        output_tokens=out_tokens,
        e2e_tok_s=e2e_tok_s,
        decode_tok_s=decode_tok_s,
        streamed=streamed,
        chunk_count=chunk_count,
        error=err,
    )


def run_workload(
    client: OpenAI,
    model: str,
    name: str,
    iters: int,
) -> list[RunResult]:
    spec = WORKLOADS[name]
    results = []
    for i in range(iters):
        r = run_one(client, model, spec["prompt"], spec["max_tokens"])
        r.workload = name
        r.iter = i
        results.append(r)
        if r.error:
            print(f"  [{name} iter {i}] ERROR: {r.error}", file=sys.stderr)
        else:
            stream_note = "streamed" if r.streamed else "buffered"
            print(
                f"  [{name} iter {i}] ttft={r.ttft_s:.2f}s "
                f"total={r.total_s:.2f}s "
                f"out={r.output_tokens}tok "
                f"e2e={r.e2e_tok_s:.1f}tok/s "
                f"({stream_note}, {r.chunk_count} chunks)"
            )
    return results


def summarize(name: str, results: list[RunResult]) -> dict:
    ok = [r for r in results if not r.error and r.ttft_s > 0]
    if not ok:
        return {"workload": name, "ok": 0, "errors": len(results)}
    streamed_any = any(r.streamed for r in ok)
    return {
        "workload": name,
        "ok": len(ok),
        "errors": len(results) - len(ok),
        "ttft_s_p50": statistics.median(r.ttft_s for r in ok),
        "ttft_s_p95": _p95([r.ttft_s for r in ok]),
        "e2e_tok_s_p50": statistics.median(r.e2e_tok_s for r in ok),
        "e2e_tok_s_p95": _p95([r.e2e_tok_s for r in ok]),
        "decode_tok_s_p50": statistics.median(r.decode_tok_s for r in ok) if streamed_any else None,
        "total_s_p50": statistics.median(r.total_s for r in ok),
        "output_tokens_p50": statistics.median(r.output_tokens for r in ok),
        "streamed_any": streamed_any,
    }


def _p95(values: list[float]) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    idx = max(0, int(round(0.95 * (len(s) - 1))))
    return s[idx]


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def cmd_baseline(args):
    client = OpenAI(base_url=args.base_url, api_key="local")
    workloads = args.workloads.split(",") if args.workloads else list(WORKLOADS.keys())

    print(f"Baseline run against {args.base_url}, model={args.model}")
    print(f"Workloads: {workloads}, iters={args.iters} each\n")

    all_results = []
    summaries = []
    for name in workloads:
        if name not in WORKLOADS:
            print(f"  unknown workload: {name}", file=sys.stderr)
            continue
        print(f"== {name} ({WORKLOADS[name]['desc']})")
        results = run_workload(client, args.model, name, args.iters)
        all_results.extend(results)
        summaries.append(summarize(name, results))
        print()

    out = {
        "mode": "baseline",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": args.base_url,
        "model": args.model,
        "iters": args.iters,
        "config": {"note": "live vLLM, configuration not introspected"},
        "summaries": summaries,
        "raw": [asdict(r) for r in all_results],
    }
    out_path = RESULTS_DIR / f"baseline_{int(time.time())}.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {out_path}")
    print("\nSummary:")
    for s in summaries:
        if s.get("ok"):
            decode = (
                f"decode {s['decode_tok_s_p50']:.1f} tok/s"
                if s.get("decode_tok_s_p50") is not None
                else "decode n/a (server buffered)"
            )
            print(
                f"  {s['workload']:8s}  ttft p50={s['ttft_s_p50']:.2f}s  "
                f"e2e p50={s['e2e_tok_s_p50']:.1f} tok/s  "
                f"out p50={int(s['output_tokens_p50'])}tok  "
                f"total p50={s['total_s_p50']:.2f}s  "
                f"({decode})"
            )
        else:
            print(f"  {s['workload']:8s}  all errored")


def cmd_sweep(args):
    print(
        "sweep mode is destructive — restarts vLLM between configs.\n"
        "Not yet implemented in this script. Run the matrix manually:\n"
        "  1. Stop vLLM:    docker stop gemma4 && docker rm gemma4\n"
        "  2. Edit start_gemma4.sh with the new flag\n"
        "  3. Start:        bash /data3/Models/start_gemma4.sh\n"
        "  4. Wait for /v1/models to respond\n"
        "  5. Re-run:       perf_sweep.py baseline --tag <config-name>\n"
        "  6. Compare results in perf_results/\n"
    )
    sys.exit(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("baseline", help="non-disruptive run against live vLLM")
    b.add_argument("--base-url", default="http://localhost:8001/v1")
    b.add_argument("--model", default="gemma4")
    b.add_argument("--iters", type=int, default=3)
    b.add_argument("--workloads", default="", help="comma list; default = all")
    b.add_argument("--tag", default="", help="optional tag stored in result file")
    b.set_defaults(func=cmd_baseline)

    s = sub.add_parser("sweep", help="destructive: restart-and-measure (manual today)")
    s.set_defaults(func=cmd_sweep)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

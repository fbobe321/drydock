# vLLM / Gemma 4 Hosting Performance Sweep — Plan & Baseline

**Status:** harness built, baseline collected (noisy), full sweep deferred until stress run completes.

**Owner artifact:** `scripts/perf_sweep.py`
**Results:** `/data3/drydock/perf_results/*.json`

---

## Why this exists

Step 1 of the Sovereign v2 framework: lock the inference performance floor before building the failure-classification loop. If the floor is wobbly, the classifier can't tell "config bug" from "real failure class."

Article-driven hypotheses to test:

| Article claim                  | Currently we use      | Test                                  |
| ------------------------------ | --------------------- | ------------------------------------- |
| Flash attention enabled        | `TRITON_ATTN`         | swap to `--attention-backend FLASH_ATTN` |
| KV cache quant `q8_0`          | `--kv-cache-dtype fp8`| swap to `auto` (fp16) — does fp8 cost quality?  |
| `temperature: 1.0`             | 0.0–0.2               | **Skipped — known bad for coding agents**     |
| `top_k: 40`                    | unset                 | **Skipped — same reason**                     |
| Q3\_K\_M quantization          | AWQ-4bit              | **N/A — different ecosystem (vLLM not llama.cpp)** |
| (vLLM-native, not in article)  |                       | `--enable-prefix-caching` on/off              |
| (vLLM-native, not in article)  | `--max-num-seqs 2`    | sweep 2 / 4 / 8                               |

---

## Harness design

`scripts/perf_sweep.py baseline` runs four workloads sized to mirror real Drydock turns:

| Workload | Input tokens | Description                                       | Max output |
| -------- | ------------ | ------------------------------------------------- | ---------- |
| short    | ~50          | chat-style                                        | 64         |
| medium   | ~2 000       | typical drydock turn (system + few tool results)  | 256        |
| long     | ~16 000      | drydock with substantial tool history             | 256        |
| xlong    | ~64 000      | heavy context, near-half max                      | 256        |

Metrics reported per workload (median + p95 across iters):

- **TTFT** — time to first content delta (prefill cost)
- **e2e tok/s** — `output_tokens / total_wall_clock` (user-visible)
- **decode tok/s** — only meaningful when content chunks span >50 ms; otherwise marked `n/a (server buffered)`
- `streamed`, `chunk_count` for diagnosis

vLLM with `--tool-call-parser gemma4` buffers chunks server-side until tool-call boundaries are resolved, so decode-only measurements are usually meaningless on this stack. **Prefer e2e tok/s.**

---

## Baseline (noisy — taken during stress run)

Captured 2026-05-01 at ~16:30 CDT against live vLLM on :8001, while the 1700-prompt stress run was active. **Numbers are contaminated** — the same prompt swung 25× in TTFT depending on whether the stress harness was mid-request. Treat as ceiling, not signal.

```
short     ttft p50=3.47s  e2e p50=9.5 tok/s   out p50=33 tok
medium    ttft p50=1.41s  e2e p50=44.8 tok/s  out p50=65 tok
```

Best uncontended values observed (single iter):
- short:  ttft 0.73 s / e2e 44.6 tok/s
- medium: ttft 1.24 s / e2e 48.4 tok/s

Even uncontended, e2e is below the ~70 tok/s documented in `CLAUDE.md`. Likely explanation: small outputs are TTFT-dominated. Long-output workload needed to isolate decode rate.

**Files:** `perf_results/baseline_1777672322.json`

---

## Sweep matrix (run after stress completes)

7 configs total. Each = restart vLLM + re-run `perf_sweep.py baseline` with all four workloads × 5 iters.

| #  | Config name           | Change vs current                                 | Hypothesis                                                                |
| -- | --------------------- | ------------------------------------------------- | ------------------------------------------------------------------------- |
| 0  | `current`             | (none — re-measure cleanly without stress)        | Establishes uncontaminated baseline                                       |
| 1  | `flash_attn`          | `--attention-backend FLASH_ATTN`                  | Article: flash improves TTFT and decode                                   |
| 2  | `kv_fp16`             | drop `--kv-cache-dtype fp8` (=fp16 default)       | Does fp8 cost quality? Compare e2e + spot-check answers                   |
| 3  | `prefix_cache_on`     | add `--enable-prefix-caching`                     | Big win for repeated system prompts (huge for Drydock TUI)                |
| 4  | `seqs_4`              | `--max-num-seqs 4`                                | Better concurrency under stress harness load                              |
| 5  | `seqs_8`              | `--max-num-seqs 8`                                | Stress test concurrency limit                                             |
| 6  | `winner_combo`        | best of 1+3 (likely flash + prefix cache)         | Combined effect                                                           |

**Total wall time:** ~7 configs × ~5 min restart + ~3 min benchmark ≈ 1 h.

**Pass criteria for "winner":**
1. Equal or better e2e tok/s on `medium` workload (drydock-typical)
2. No quality regression on a 5-PRD spot check (run `shakedown.py` on 5 PRDs, must still pass)
3. No timeouts on `xlong` workload (catches OOM under long contexts)
4. Either equal-or-lower TTFT on `medium`, or a documented justification why higher TTFT is acceptable

---

## What's NOT in the sweep (and why)

- **`temperature: 1.0` / `top_k: 40`** — bad advice for coding agents. We escalate temp deliberately as a loop-break lever (`agent_loop.py:2032`), not a default. Skipped.
- **Q3\_K\_M (Unsloth GGUF)** — different ecosystem (llama.cpp). Becomes relevant only when bringing up the customer-hardware portability path (Phase 1 tail of the Sovereign PRD).
- **`gpu-memory-utilization` higher than 0.95** — already at the practical ceiling.
- **Tensor-parallel size > 2** — only have 2 GPUs.

---

## Next actions

1. ⏸ Wait for stress harness to drain to `0/0` (ETA ~10–12 h from 16:30 CDT 2026-05-01).
2. ▢ Run `current` config clean baseline (5 iters, all four workloads).
3. ▢ Run configs 1–5 sequentially, comparing against `current`.
4. ▢ Build winning combo, validate via `shakedown.py` 5-PRD spot check.
5. ▢ Lock the winner into `start_gemma4.sh` and `CLAUDE.md`.
6. ▢ Move on to Step 2 of the framework: **manual failure triage** of `MODEL_SHORTCOMINGS.md` + Admiral logs + `BASELINE_412.md` to seed the classification taxonomy.

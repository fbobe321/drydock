# Jetson AGX Orin vs Local vLLM — gemma-4-26b Benchmark

**Date:** 2026-05-03 12:30 UTC
**Jetson endpoint:** `http://192.168.50.19:8080` (llama.cpp / llama-server, GGUF)
**Local vLLM:** `http://localhost:8001` (vLLM Docker, AWQ-4bit, 2× RTX 4060 Ti)
**Model:** Both serve `gemma-4-26b` (26B MoE, ~4B active params)

---

## Headline numbers

| Workload | Jetson e2e | vLLM e2e | Speedup | Jetson TTFT | vLLM TTFT |
|----------|------------|----------|---------|-------------|-----------|
| short  (~50 in, 64 out)   | **14.5 tok/s** | 61.5 tok/s | vLLM 4.2× | 0.72 s | 0.53 s |
| medium (~2 K in, 256 out) | **15.1 tok/s** | 46.6 tok/s | vLLM 3.1× | **0.30 s** | 1.47 s |

Jetson decode-only rate (measurable because llama.cpp actually streams):
**15–17 tok/s** sustained.

---

## What's surprising

1. **Jetson wins TTFT on the medium workload** (0.30 s vs 1.47 s). Two
   plausible reasons:
   - The local-vLLM medium TTFT was captured during the active stress
     run (queue contention). The "uncontested" baseline run earlier
     showed similar 1.47 s — but that was also during the stress
     wind-down. Worth re-measuring vLLM TTFT on a fully idle box.
   - llama.cpp's GGUF prefill on the Jetson AGX Orin's tensor cores
     may be more efficient than vLLM's AWQ-4bit dequantize-then-prefill
     pass on Ada-Lovelace cards for moderate prompt sizes.
2. **Decode rate is where vLLM crushes Jetson** — 3–4× faster sustained
   token generation. The 4060 Ti's higher memory bandwidth (288 GB/s × 2)
   beats the AGX Orin's unified 204 GB/s for transformer decode.
3. **Jetson llama-server returns `reasoning_content` by default** — the
   model is in thinking mode out of the box. Affects how the harness
   counts content chunks; `perf_sweep.py` was patched to track both
   `delta.content` and `delta.reasoning_content` (see
   commit accompanying this doc).

---

## Practical implications for "Jetson testing for a label"

- **Single-user inference, short responses (≤256 tokens):** Jetson is
  fine. ~17 s for a 256-token response is acceptable for a real-time
  coding-assistant turn that includes some thinking.
- **Long-context analysis (10K+ input):** Jetson degrades. Was queue-
  blocked for 7+ minutes on a 64K xlong workload during the initial run
  — had to abort. vLLM handles the same in seconds.
- **Stress harness / batch evaluation:** Use vLLM. Jetson would take
  ~5× the wall-clock time for the 1658-prompt PRD suite.
- **Air-gapped / edge deployments where Jetson is the *only* option:**
  Defensible. ~15 tok/s is bearable; just budget 10–60 s response times
  in the UX.

---

## How to reproduce

```bash
# Foreground, both workloads, fixed harness
PYTHONUNBUFFERED=1 /home/bobef/miniconda3/bin/python3 -u \
    /data3/drydock/scripts/perf_sweep.py baseline \
    --base-url http://192.168.50.19:8080/v1 \
    --model gemma-4-26b \
    --workloads short,medium \
    --iters 5
```

DO NOT run `--workloads short,medium,long,xlong` against the Jetson
without watching the wall clock — xlong (64 K input tokens) appears to
queue-block llama-server for several minutes on this hardware.

---

## What changed in `perf_sweep.py`

- `delta.reasoning_content` is now treated as content for TTFT and
  chunk-counting. Without this, the Jetson's thinking-mode output
  registered as 0 chunks → harness reported "all errored" even on
  successful runs. Fix is forward-compatible with vLLM (which emits
  `delta.content` exclusively).

---

## Operator notes

- **Jetson endpoint is read-only from drydock's perspective.** Per the
  long-standing rule (see project memory), drydock does not admin the
  Jetson. The benchmark only hits the OpenAI-compatible API.
- **`stream=true` is the default on llama-server.** Streaming worked
  cleanly through the openai client.
- **Reasoning mode:** on this Jetson build (`b1-e5f070a`), the default
  chat format is "Content-only" but the model still emits reasoning. To
  disable thinking-mode emission, the operator would set
  `reasoning_format: "none"` and `reasoning_in_content: true` per
  request. Not done in this benchmark — measured the as-shipped behavior.

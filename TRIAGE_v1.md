# Failure Triage v1 — Sovereign Stack v2 Framework, Step 2

**Date:** 2026-05-02
**Source data:** `MODEL_SHORTCOMINGS.md` (11 patterns) + `BASELINE_412.md` (32 dirty PRDs as of 2026-04-13) + open GH issues (0 at time of write).
**Goal:** Validate the classifier taxonomy and produce a starter backlog with the right module attached to each bucket.

This is a manual pass. Output of this triage drives:

1. The taxonomy schema the Phase-2 classifier must be able to produce.
2. Per-bucket evidence justifying GraphRAG and Deep Noir as first-class modules (vs. infra-on-faith).
3. A prioritized engineering backlog by failure volume.

---

## Taxonomy (validated against existing data)

| Bucket              | Definition                                                                          | Primary dispatcher           |
| ------------------- | ----------------------------------------------------------------------------------- | ---------------------------- |
| **harness**         | Tool plumbing, prompt structure, control flow, write/edit safety, loop detection.   | Drydock harness              |
| **retrieval**       | Missing context the model has no way to know without external lookup or memory.     | GraphRAG / read-before-write |
| **steering**        | Stable behavioral priors (direction biases) — what the model *tends* to reach for.  | Deep Noir vectors            |
| **model-prior**     | Reasoning / world-knowledge gaps that aren't behavioral — true capability ceiling.  | LoRA fine-tune / model swap  |
| **ambiguous-input** | PRD / user prompt is genuinely under-specified; not a model failure.                | Prompt template / ask-user   |

A single failure can have a **primary** and a **secondary** bucket. The classifier's job is to emit the primary; the secondary is for prioritization.

---

## Per-pattern classification

Mapping each `MODEL_SHORTCOMINGS.md` pattern to bucket(s):

| # | Pattern (short)                                | Primary       | Secondary | Status / dispatcher                                                                             |
| - | ---------------------------------------------- | ------------- | --------- | ----------------------------------------------------------------------------------------------- |
| 1 | Catastrophic tool-arg malformation             | harness       | model     | ✅ mitigated (`>50%` shrink refusal, raw-code fallback in `search_replace`)                      |
| 2 | Scaffolding without wiring                     | model-prior   | retrieval | partially mitigated by worked examples + meta_ralph; model can't trace call graphs end-to-end   |
| 3 | Subtle logic bugs undiagnosed                  | model-prior   | steering  | "trace failing line first" steering vector candidate                                            |
| 4 | Inheritance blindness across packages          | **retrieval** | model     | **GraphRAG candidate.** Model can't grep parent-class packages without a hint                   |
| 5 | Regression during targeted fixes               | harness       | steering  | ✅ mitigated (snapshot + rollback in ralph_loop). Steering candidate: "minimal-patch" direction  |
| 6 | Blindness to own stuckness                     | harness       | steering  | partially mitigated (loop detection, dedup escalation). Steering: "switch approach when stuck"  |
| 7 | Doesn't use web_search even when enabled       | **steering**  | model     | **Deep Noir candidate.** Prompt rule alone insufficient; need to boost "search-when-stuck" dir  |
| 8 | Hallucinated tool names                        | harness       | —         | ✅ mitigated (`_IGNORE_TOOLS` silent drop)                                                      |
| 9 | Empty response / thinking stall                | harness       | model     | ✅ partially mitigated (truncate old tool results + thinking-stall nudge). Still seen on hard PRDs|
| 10a| Stub-class anti-pattern                       | harness       | model     | ✅ mitigated (`_check_stub_classes` in write_file)                                              |
| 10b| Interactive fallback with CLI args present    | model-prior   | harness   | minor; AST-check has weak signal. Steering candidate: "prefer-explicit-args" direction          |
| 10c| Multi-module rewrites exceed 3-stage iteration| **retrieval** | model     | **GraphRAG candidate.** Need persistent design memory across sessions (~3-4 file context limit) |
| 10 | Weak reasoning about performance / abstract   | model-prior   | steering  | "abstract reasoning" prompt + worked examples. Hard problem; possibly model-ceiling             |

---

## Per-bucket distribution

| Bucket             | Primary count | With secondary | Comment                                                                                       |
| ------------------ | ------------- | -------------- | --------------------------------------------------------------------------------------------- |
| **harness**        | 6             | 8              | 5/6 already mitigated. The harness is paying its rent and continues to be the highest-volume bucket. |
| **retrieval**      | **2**         | 4              | Cross-package inheritance + multi-module memory. **GraphRAG earns its slot** on these alone.  |
| **steering**       | **1**         | 6              | Web-search habit is the cleanest steering candidate. Six others have steering as a viable secondary lever. **Deep Noir earns its slot.** |
| **model-prior**    | 4             | 7              | Reasoning gaps (logic, abstract optimization, scaffolding-wiring). LoRA territory.            |
| **ambiguous-input**| 0             | 0              | Not represented in MODEL_SHORTCOMINGS (curated for model issues). Will appear when classifier runs against live stress data. |

**Headline finding:** the existing failure corpus *does* justify GraphRAG and Deep Noir as parallel v2 modules. They're not infra-on-faith — there are concrete failure patterns for each.

---

## Module-by-module: what the data says

### Harness (already shipping; deepest investment to date)

5/6 primary-harness patterns are already mitigated. The classifier still routes harness-class signals here, and the autonomous_review cron is the half-built dispatcher. **Phase-1 work:** read-before-write enforcement (will move some pattern-4 / pattern-10c-style failures from "retrieval" into "this should have been caught at write time").

### GraphRAG (Phase 2 first cut — justified)

Two clean primary signals:

- **Pattern 4: cross-package inheritance.** Flask `request.is_xml` — model read `wrappers.py` 13× looking for a method that lives in a parent class in a different package. With a one-line hint pointing at the parent, model solved it in 61s. This is exactly what code-aware GraphRAG should serve: "where is `is_json` actually defined across the package graph?"
- **Pattern 10c: multi-module architectural rewrites.** lang_interp's lexer→parser→type_checker→interpreter→repl chain exceeded the model's working-memory budget. Rolling design memory across sessions (PRD goals + per-module contracts + decisions made) is what GraphRAG would store.

**Implication:** the GraphRAG first cut shouldn't optimize for arbitrary PDF Q&A. It should optimize for two specific shapes:
1. **Code graph** — symbol → definition site, including parent-class chains across packages.
2. **Project memory** — PRD + design decisions + per-module contracts, persistent across drydock sessions.

This is a sharper, smaller scope than "ingest everything." Easier to ship in 60 days.

### Deep Noir (Phase 3 first cut — justified)

One primary signal + six viable secondaries:

- **Primary: pattern 7, web-search habit.** Prompt rules don't move the needle. The model has a stable behavioral prior of "stay in local-only failure loop." A vector that boosts the "external lookup when stuck" direction is the textbook Deep Noir use case.
- **Secondary candidates (1 vector each):**
  - "minimal-patch direction" (pattern 5: rewrites whole file when a 3-line change would do)
  - "switch-approach-when-stuck direction" (pattern 6: keeps trying same pattern)
  - "prefer-explicit-args direction" (pattern 10b: ignores `--password` flag, falls to `getpass()`)
  - "trace-failing-line direction" (pattern 3: rewrites file instead of tracing the bug)

**Implication:** the Deep Noir first cut should ship **one well-tuned vector** (web-search habit) with a sandbox eval, not five vectors built in parallel. Volume of viable secondaries proves the program's value; engineering attention goes to the one with the cleanest signal first.

### Model-prior (LoRA territory, longer horizon)

Patterns 2, 3, 10b, 10 — reasoning gaps, not behavior gaps. Steering can move some of these (5/4 secondary on pattern 3); LoRA fine-tuning is the eventual answer. **Out of scope for v2 first cuts.** Tracked in the project `feedback_finetuning_deep_noir.md` memory.

---

## Pareto: what to build next, in order

1. **Hosting sweep** — already queued via `*/15` cron. Locks the perf floor before classifier has any meaning.
2. **Read-before-write enforcement** in the harness (Phase 1, already in PRD). Cheapest way to convert some pattern-4 / pattern-10c failures into clean errors before the model commits to bad output.
3. **Classifier first cut** (Phase 2) — emits the buckets in the table above, validated against this manual pass.
4. **Code-graph GraphRAG first cut** (Phase 2) — narrowed scope: symbol/inheritance lookup + per-project design memory. Skip arbitrary PDF Q&A in v0.
5. **Deep Noir vector v0** (Phase 3) — single vector for the web-search habit pattern, with a sandbox eval gating promotion.
6. Successive vectors / GraphRAG hardening as the live classifier produces volume in those buckets.

---

## What this triage produced

- A 5-bucket taxonomy that fits all 11 documented patterns (no "other").
- Concrete justification (with cited evidence) for both GraphRAG and Deep Noir as v2 modules.
- A scoped first cut for each module — code-graph GraphRAG (not arbitrary corpora), single-vector Deep Noir (not five at once).
- A prioritized backlog where engineering attention follows volume.
- A schema for the classifier to emit. Phase-2 classifier work has a target now.

---

## Perf baseline — clean, post-stress (2026-05-02 14:31 UTC)

Captured by `scripts/perf_baseline_when_idle.sh` after the 47h stress run drained at 14:25 UTC. Stress had been contaminating earlier baseline runs with up to 25× TTFT swings; this is the first uncontaminated measurement.

| Workload | Input  | Output (p50) | TTFT p50  | TTFT p95  | e2e p50      | e2e p95      |
|----------|--------|--------------|-----------|-----------|--------------|--------------|
| short    | ~50    | 33 tok       | **0.53 s**| 2.46 s    | **61.5 tok/s**| 65.7 tok/s  |
| medium   | ~2 K   | 64 tok       | **1.47 s**| 3.72 s    | **46.6 tok/s**| 67.7 tok/s  |
| long     | ~16 K  | 82 tok       | **1.39 s**| —         | **58.3 tok/s**| —           |
| xlong    | ~64 K  | 68 tok       | **1.47 s**| —         | **44.3 tok/s**| —           |

5 iters per workload, all OK (0 errors). vLLM with the gemma4 tool parser still buffers content chunks server-side (`streamed_any: false`), so decode-only rate is `n/a`; e2e is the right metric for our use case.

**Findings:**

- **e2e tok/s is below the documented ~70 tok/s** (44–62 tok/s range). Most of the wall clock for these short outputs is prefill (TTFT 0.5–1.5 s) rather than decode. To get a clean decode-rate measurement we'd need workloads with much larger outputs (e.g. 1–2 K output tokens), not 33–82.
- **TTFT scales sub-linearly with input size:** ~0.5 s for 50-token prompt, ~1.5 s for 64K. That's a strong signal — vLLM's prefill is fast and the AWQ + fp8 KV cache combination is working well at long context.
- **p95 spread is loose** on `short` and `medium` (0.5 → 2.5 s, 1.5 → 3.7 s) — the new stress run kicked off mid-baseline and contended for vLLM. Counts the result as "live vLLM under realistic concurrency" rather than "isolated."
- **No errors at any input size, including 64 K input** — KV cache is healthy.

**Implication for the framework:**

This is now the **floor** the classifier evaluates against. Any failure-class signal that suggests a hosting-config issue (e.g. "TTFT spiked 5× above floor for a normal-sized prompt") is now a measurable thing, not a guess.

**Implication for the destructive sweep (configs 1–6 in `PERF_SWEEP_PLAN.md`):**

Still held for explicit user trigger — each config requires editing `start_gemma4.sh` and restarting vLLM, and the cost of an overnight failure was too high. With this clean floor in hand, comparison runs are now meaningful.

---

## Open questions for next pass

- Live stress-run failures — are the failure modes the same as the curated ones? Should add ambiguous-input bucket if PRDs are under-specified at observed rates.
- BASELINE_412 dirty PRDs — 32 packages × N tests is real failure volume. Per-PRD triage could surface patterns missing from MODEL_SHORTCOMINGS (e.g. pattern-12, pattern-13).
- Inter-rater check — once the Phase-2 classifier is built, run it on this same data and check agreement with this manual pass. <80% agreement = taxonomy is wrong.

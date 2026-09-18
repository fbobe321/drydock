# Technical Specification — Cache-Aware Modular Context Runtime

- **Project:** Drydock
- **Component:** Modular Context Runtime (MCR)
- **Subsystem:** Cache-Aware Context Paging
- **Related:** Ratchet, ERatchet, Context Scheduler, Compute Governor
- **Status:** Proposed

---

## 0. Implementation status (added 2026-09-18 — what already exists)

This spec was written after `drydock/context_runtime.py` had shipped MCR phases 1–5, and it
independently arrives at the ordering rule that was already measured empirically
(`docs/modular_context_runtime_prd.md` Appendix A.1: re-prefill cost scales with how early a change
lands — tail 0.24×, middle 0.59×, head 0.98× of a cold prefill). Honest map:

| § | Item | Status |
|---|---|---|
| 3, 5 | stable→volatile ordering; keep-prefix/change-suffix | ✅ built (`VIEW_ORDER`) + measured |
| 6 | immutable blocks / versioned modules | ✅ built (append-only store, `version`) |
| 10 | cache reuse score | ✅ built (`prefix_reuse` → `reuse_pct`) |
| 15 | ratchet as cache boundary | ✅ built (`drydock/context_ratchet.py`) |
| 20 | context store | ✅ built (`ContextStore`) |
| 4 | six explicit cache zones | ⚠️ partial — 5 residency classes are coarser than zones 0–5 |
| 9 | view fingerprint / LCP | ⚠️ partial — **keyed semantically, see §8 gap below** |
| 16 | speculative stays late | ⚠️ partial — holds by accident (hypotheses land in WORKING), not by rule |
| 19 | layout planner | ⚠️ partial (`build_view`) |
| 25 | context GC | ⚠️ partial (archive exists; no cold-module sweep) |
| 27 | telemetry | ⚠️ partial (`research_telemetry.py`; no cache fields) |
| 7, 8 | **manifest + deterministic fingerprints** | ❌ **not built — the foundation gap** |
| 11, 13 | multi-objective utility; effective mount cost | ❌ not built (selection is priority-only) |
| 12, 28 | prefill/decode split; ECC metric | ❌ not built |
| 17, 18 | prefix-sharing context forks | ❌ not built |
| 21, 22, 23 | scheduler; cache adapter; capability levels | ❌ not built |
| 24, 26, 31 | cache-aware compaction; page faults; governor | ❌ not built |

**The §8 gap is a live correctness bug, not a missing feature.** `prefix_reuse()` compares
`(context_id, version, level)` — semantic identity. Cache identity depends on the actual serialized
bytes/tokens, so the current predictor can report reuse that the server will not grant (same
version, different serialization) *and* report divergence where bytes are identical. Per §32 this is
item 1 and should be fixed before anything else is built on top.

**Empirical note supporting §22/§23:** backends already observed to differ. `.20` reports
`usage.prompt_tokens_details.cached_tokens` and hit 100% on an identical resend; `.21`
(vLLM v0.26.0 + AWQ/MoE) defaulted to `enable_prefix_caching=False` and reported no
`cached_tokens` at all. A capability-tiered adapter is required, not optional.

**Connection to the scaling research (§18).** `docs/compute_optimal_agent_scaling_prd.md` recorded
that width bought nothing on solvable tasks (64 agents ≈ 10× tokens, same result) and that
concurrent agents evict each other's prefix blocks. §18 says that is a property of *how forks are
currently constructed*, not of fan-out itself: deliberately shared prefixes move 4×(80K+5K)=340K
toward 80K+4×5K=100K. The width conclusion should be re-tested once prefix-sharing forks exist.

---

## 1. Purpose

Drydock's Modular Context Runtime treats an LLM context window as a managed working set rather than
an append-only transcript. However, arbitrary context modification conflicts with transformer KV
caching. If a prompt is `[A][B][C][D]` and Drydock removes `B`, giving `[A][C][D]`, the cached
representations for `C` and `D` generally cannot be reused in their original positions — their
attention history and token positions have changed.

Naive modular paging could therefore save context tokens while causing substantial prefill
recomputation. This subsystem makes modular context management cache-aware. The objective:

> **Minimize resident context while maximizing reusable prefix state.**

---

## 2. Core Constraint

For a causal transformer, `KV_t = f(x_1, x_2, …, x_t)`: a token's cached representation depends on
everything preceding it. Changing an earlier portion invalidates subsequent cached state.

```
CALL 1   [A][B][C][D]      all cached
CALL 2   [A][C][D][E]      A is a reusable prefix; after divergence, C and D cannot be
                           assumed reusable from the previous sequence
```

Therefore: **context modularity and KV modularity are not equivalent.** MCR must account for this
explicitly.

---

## 3. Primary Architectural Principle

Order context **most stable → most volatile**, not chronologically:

```
┌─────────────────────────────┐
│ SYSTEM / KERNEL             │  Very stable
│ PROJECT BASE IMAGE          │  Very stable
│ VERIFIED KNOWLEDGE          │  Stable
│ TASK STATE                  │  Moderately stable
│ MOUNTED REFERENCE MODULES   │  Variable
│ CURRENT WORKING SET         │  Volatile
│ RECENT EVENTS / TOOL OUTPUT │  Highly volatile
└─────────────────────────────┘
```

Changes should preferentially occur toward the end, maximizing common-prefix reuse.

---

## 4. Context Zones

- **Zone 0 — Kernel.** System instructions, Drydock operating protocol, tool semantics. Lifetime:
  many tasks. Mutation: extremely low. Cache priority: maximum.
- **Zone 1 — Base Image.** Repository architecture, project conventions, validated reusable
  knowledge, coding standards. Lifetime: task/session/project. Mutation: low.
- **Zone 2 — Ratcheted State.** Verified accumulated discoveries ("parser bug localized to
  parser.py"; "do not replace recursive parser with regex"; "API requires preserve_comments=True").
  Changes mainly when Ratchet accepts an improvement.
- **Zone 3 — Task Working Set.** Objective, subproblem, relevant source, active hypothesis,
  important errors. Moderately volatile.
- **Zone 4 — Mounted Pages.** Temporarily retrieved: documentation, CLI help, archived branch,
  previous experiment, dependency source. Explicitly mountable/unmountable.
- **Zone 5 — Scratch.** Recent tool output, current experiment, temporary observations. Usually the
  suffix.

---

## 5. Cache-Aware Context Rule

Prefer **KEEP PREFIX / CHANGE SUFFIX** over **EDIT MIDDLE**.

```
Bad:    [KERNEL][PROJECT][CLI][STATE][WORK]  -- remove CLI -->  invalidates everything after it
Better: [KERNEL][PROJECT][STATE][WORK][CLI]  -- unmounting CLI changes only the suffix
```

---

## 6. Immutable Context Blocks

Once a stable module is in the reusable prefix, treat its serialized representation as immutable.
Do not modify `PROJECT_STATE_v1` in place — create `PROJECT_STATE_v2` and switch versions at an
intentional boundary. Analogous to immutable memory pages / copy-on-write storage.

---

## 7. Context Manifest

Each inference request should carry a manifest, which also becomes telemetry:

```yaml
context_view: view_1842
modules:
  - {id: kernel-v7,            tokens: 4200,  cache_class: immutable}
  - {id: drydock-project-v31,  tokens: 11840, cache_class: stable}
  - {id: ratchet-state-77,     tokens: 3300,  cache_class: checkpoint}
  - {id: parser-task,          tokens: 5700,  cache_class: working}
  - {id: pytest-reference,     tokens: 2100,  cache_class: pageable}
  - {id: scratch-1842,         tokens: 6400,  cache_class: volatile}
```

---

## 8. Module Fingerprints

Every serialized module gets a deterministic fingerprint `H_i = Hash(Tokenize(Module_i))`.

This matters: two modules that are semantically identical but serialized differently may tokenize
differently and destroy prefix identity. **Cache identity must depend on the actual
serialized/tokenized representation, not merely the semantic context ID.**

Store: `module_id, version, content_hash, token_hash, token_count, position, cache_policy`.

---

## 9. Context View Fingerprint

For modules `M_1…M_n`, compute cumulative prefix fingerprints `P_i = H(M_1 ‖ … ‖ M_i)`. On the next
inference, find `LCP(Context_t, Context_{t+1})`. Everything before divergence is potentially
reusable under a backend with compatible prefix caching. This is a fundamental scheduler metric.

---

## 10. Cache Reuse Score

`CR(V) = reusable prefix tokens / total prefill tokens`. Candidate A (90K reusable / 10K new) scores
0.90; candidate B (25K reusable / 75K recomputed) scores 0.25. If both supply sufficient relevant
context, prefer A.

---

## 11. Context Selection Is Multi-Objective

Rather than maximizing `Relevance(V)` subject to `Tokens(V) ≤ Budget`, use:

`Utility(V) = αR + βC + γQ − δT`

with R = relevance, C = cache reuse, Q = context quality/confidence, T = token/prefill cost.
Verified progress should eventually determine the learned weights.

---

## 12. Prefill Cost Must Be First-Class

`C_total = C_prefill + C_decode`. For long contexts, repeatedly rebuilding large prefixes is
expensive even when generation is short. Record per call: `prompt_tokens`, `cached_prompt_tokens`,
`uncached_prompt_tokens`, `output_tokens`, `prefill_ms`, `decode_ms`, `cache_hit_ratio` — where the
backend exposes them.

---

## 13. Mount Cost

Every module has an estimated mounting cost. Mounting a 2K module can cost far more than 2K tokens
if its placement destroys reuse for a long suffix:

`EffectiveMountCost = NewTokens + InvalidatedPrefix/SuffixWork`

---

## 14. Never Optimize Cache at the Expense of Correctness

Priority order: **1. correctness · 2. required constraints · 3. verified state · 4. task relevance ·
5. cache efficiency.** Cache optimization operates only among otherwise-valid context views. The
scheduler must never omit required information to preserve cache reuse.

---

## 15. Ratchet as a Natural Cache Boundary

```
Agent work → Verifier → fitness improves → GitCheckpoint → ContextCheckpoint → new stable prefix
[RATCHET STATE 77][SCRATCH]  --success-->  [RATCHET STATE 78][new scratch]
```

Rather than continuously rewriting stable context after every observation, promote changes at
verified checkpoints.

---

## 16. Speculative Context Must Stay Late

Unverified information must not enter the stable prefix. A failed hypothesis then disappears
cheaply; a verified one is promoted at the next checkpoint. This aligns epistemic confidence with
cache locality:

> **More verified ⇒ more stable ⇒ earlier.**

---

## 17. Context Forks and KV Forks

For ERatchet's `BASE → {A,B,C,D}`, all four candidates should share an identical stable prefix and
diverge only afterward:

```
                          ┌── [A]
[KERNEL][PROJECT][STATE] ─┼── [B]
                          ├── [C]
                          └── [D]
```

Drydock should intentionally construct requests sharing an identical prefix rather than having four
agents each rebuild the whole context.

---

## 18. This Changes the Economics of Fan-Out

With an 80K stable base and 5K per branch, four agents naively process ≈ `4(80K+5K) = 340K` prompt
tokens. With reusable prefix caching the expensive work moves toward `80K + 4(5K) = 100K`, depending
on backend. **Modular context can make search fan-out cheaper**, provided branches are constructed
correctly — directly connecting this work to the Ratchet scaling research.

---

## 19–21. Proposed Modules

- **`drydock/context/layout.py`** — classify modules by stability; order them; maximize common
  prefixes; estimate invalidation; compute budget; produce the serialized view.
  `planner.build_view(task, required, optional, previous_view, token_budget)` →
  `ContextView(modules, token_count, reusable_prefix_tokens, estimated_prefill_tokens)`.
- **`drydock/context/store.py`** — `put / get / archive / tombstone / versions / dependencies`.
  Retains complete content independently of residency.
- **`drydock/context/scheduler.py`** — determine required modules, retrieve dependencies, select
  resolution, assign residency, choose ordering, estimate cache reuse, enforce budget, request
  page-in/eviction. Backend-independent.

---

## 22–23. Cache Adapter and Graceful Degradation

```python
class CacheAdapter:
    def capabilities(self): ...
    def reusable_prefix(self, old_view, new_view): ...
    def cache_stats(self): ...
    def invalidate(self, context_view): ...
```

Backends: vLLM, llama.cpp, Ollama, OpenAI-compatible, Anthropic-compatible, others. Drydock must not
assume every provider exposes explicit KV-cache handles. Capability levels:

- **Level 0 — no cache information.** Drydock still performs modular context management.
- **Level 1 — automatic prefix caching.** Drydock constructs cache-friendly identical prefixes and
  lets the backend handle caching.
- **Level 2 — explicit cache capabilities.** Drydock exploits backend-specific primitives.

---

## 24. Cache-Aware Compaction

If only `ARCHIVE` needs compressing in `[KERNEL][PROJECT][VERIFIED][WORK][ARCHIVE]`, do not rewrite
`PROJECT`. Stable module serialization must remain deterministic: even harmless rewording ("The
project uses pytest." → "Pytest is used by this project.") alters tokenization and needlessly
invalidates reuse. **Do not rewrite stable context for cosmetic reasons.**

---

## 25. Context Garbage Collection

Periodically identify cold modules — completed tool instructions, superseded hypotheses, old
transient errors, raw output already distilled, dead search branches. GC means removing from
*residency*, not destroying: `resident → compact if useful → archive → retain pointer`. Stable
prefix modules should be disturbed as little as possible.

---

## 26. Context Page Fault Protocol

```
CONTEXT_FAULT:
    query: "Why was regex parser rejected?"
    required_resolution: evidence
```

Drydock: pause the current reasoning slice → search context storage → identify the module →
determine its cheapest cache-compatible placement → construct the next view → resume. The complete
reasoning trajectory remains in Drydock state even though the model invocation changes.

---

## 27. Telemetry

```json
{"context_tokens": 82431, "reusable_prefix_tokens": 71322, "uncached_tokens": 11109,
 "cache_reuse_ratio": 0.865, "modules_resident": 14, "modules_mounted": 2,
 "modules_evicted": 1, "page_faults": 0, "prefill_ms": 843, "decode_ms": 2210}
```

---

## 28. New Research Metric

**Effective Context Cost:** `ECC = uncached prefill tokens + λ(decode tokens)`, giving

> **ContextEfficiency = ΔVerifiedFitness / ECC**

This compares context policies by the compute they actually consume rather than nominal context
length.

---

## 29. Critical Experiment

| Policy | Paging | Compaction | Cache-aware layout |
|---|---|---|---|
| Append-only | No | No | No |
| Global compact | No | Yes | No |
| Modular | Yes | Local | No |
| Cache-aware modular | Yes | Local | Yes |

Measure solve rate, total prompt tokens, uncached prompt tokens, cache hit ratio, time-to-solution,
GPU-seconds, max resident context, page faults, repeated mistakes, verifier progress.

**The key comparison is Modular vs Cache-Aware Modular**, because it isolates whether intelligent
layout actually recovers the compute that arbitrary paging would otherwise waste.

---

## 30. A Deeper Research Hypothesis

The optimal context is **not** simply the smallest relevant context:

> `Context* = argmax (reasoning utility / incremental inference cost)`

Keeping an extra 10K tokens that aren't immediately necessary can be *cheaper* than removing them if
removal destroys reuse of a huge cached prefix. The scheduler may intentionally retain currently
irrelevant context for its cache locality — analogous to an OS keeping a page in RAM because
evicting and reloading costs more.

---

## 31. Integration With the Compute Governor

The governor manages four resources together: **Model + Compute + Context + Cache.** Per operation:
which model; which modules; at what resolution; which stay resident; which are evicted; how much
prefix is reusable; one execution or several forks; serial or parallel; how much additional compute;
escalate to a stronger model. This should interact with Ratchet's marginal-progress measurements.

---

## 32. Implementation Priority

1. **Context manifests and deterministic module serialization.** Without this you cannot reliably
   measure cache reuse.
2. **Prefix-difference telemetry.** Before changing behaviour, measure how much of successive
   Drydock prompts is actually identical.
3. Stable → volatile context ordering.
4. Module-level mounting/eviction.
5. Ratchet ContextCheckpoints.
6. Copy-on-write context forks for ERatchet.
7. Cache-aware scheduler.
8. Only then attempt automatic optimization.

**Architectural rule: MCR must never require access to the model's actual KV tensors.** If a backend
later permits explicit reuse, that is an optimization layer. The basic architecture works simply by
deliberately constructing prompts so successive calls and parallel forks share large identical
prefixes.

**Corollary — context isolation no longer requires an agent.** Fork the context state, execute the
fork with whatever model/worker is appropriate, and either merge its verified delta or discard it.
Agents become one way to *execute* context branches rather than the mechanism that *creates* them.

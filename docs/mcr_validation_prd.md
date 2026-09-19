# PRD — Drydock Modular Context Runtime Validation Harness

- **Project:** Drydock
- **System Under Test:** Modular Context Runtime (MCR) + cache-aware paging
- **Related:** Ratchet, ERatchet, Context Scheduler, Context Store, Cache Adapter
- **Purpose:** Prove modular context is correct, preserves required knowledge, reduces active
  context, and does not lose its gains to KV/prefix-cache invalidation.

> **This is a validation PRD, not an implementation PRD.** The implementation exists
> (`context_runtime.py`, `context_window.py`, `context_ratchet.py`, `context_inject.py`,
> `context_profile.py`, `prefix_telemetry.py`, `holdout.py`, `verifier_integrity.py`). The task is
> to test the pieces independently *before* asking whether the whole improves Terminal-Bench or
> SWE-bench.

---

## 0. MEASURED RESULT (2026-09-18) — the design premise is wrong

Two instrumented live runs (single-shot and a 6-round `/ratchet`, 29 and 88 context assemblies)
produced **zero paging downgrades**. The assembly log and composition profile explain why, and it is
not a threshold:

**Composition of a real 14,126-token window**

| | tokens | share |
|---|---:|---:|
| assistant (the model's own turns) | 11,577 | **82%** |
| system prompt (pinned, unpageable) | 1,755 | 12% |
| tool-call arguments | 1,118 | 8% |
| **tool results** | **767** | **5%** |

Both the MCR PRD and the cache-aware spec assume "the transcript's bulk is tool results", and the
implementation pages tool results. **In Drydock tool results are ~5% of the window**, because tool
output is already bounded at the tool layer. A pager that cannot touch 82% of the window cannot
manage the window.

**And even that 5% is unreachable.** A large tool result is always the NEWEST message when it
arrives — correctly protected, since the model just asked for it — and by the next turn `compact()`
has truncated it, so it is below the modularization threshold and no longer a candidate. Compaction
structurally wins the race every time.

**What does work, unintentionally:** the store retains the full text that compaction destroys
(4 modules × ~5,893 tokens preserved while the transcript held truncated copies). That is
*recoverability*, one of §3's four criteria — achieved as a side effect rather than by design.

**Implication for this PRD:** Gates 1 and 2 pass, Gate 8 (efficiency vs baseline) cannot be reached
by the current design. Re-targeting is required before the higher gates mean anything: modularize
the **assistant tail and tool-call arguments**, which are 90% of the window, rather than tool
results. Until then the ladder is measuring a mechanism that has nothing to act on.

---

## 1. Problem

A successful run depends on many mechanisms at once:

`Selection → Paging → Serialization → Inference → Cache → Retrieval → Verification`

So a failed benchmark does not say whether the wrong context was selected, required information was
evicted, a module failed to reload, serialization changed the prefix, cache reuse failed, a
tombstone lost something critical, isolation leaked, or the model simply failed the task. The suite
must isolate these variables.

**Already observed, and exactly why this PRD exists:** three separate components this session
reported success while doing nothing — prefix telemetry recording all zeros, the ratchet bridge
recording 1 of 3 rounds, and the modular window registering modules then degrading none. Each took
multiple live runs to diagnose because the resident context could not be reconstructed after the
fact.

## 2. Validation Philosophy

Do not begin with a large coding benchmark. Build a deterministic synthetic environment where
correct behaviour is known in advance.

> **Unit → Deterministic Context → Cache → Reasoning → Ratchet → Long Horizon → Real Benchmark**

Every lower level must pass before results from the next are interpreted.

## 3. Primary Success Criteria

- **Correctness** — required information is available when needed.
- **Isolation** — irrelevant/speculative context does not contaminate unrelated operations.
- **Recoverability** — evicted information can reliably be restored.
- **Efficiency** — achieved with less resident context and/or less effective inference compute.

> **Efficiency gain without capability loss.**

## 4. Dedicated Test Harness

`drydock context-test --suite {unit|paging|cache|isolation|ratchet|long-horizon|all}`.
Every test emits structured results rather than relying on transcript inspection.

## 5. Level 0 — Deterministic Non-LLM Tests

Eliminate the model entirely. Cover: module creation, addressing, hashing, deterministic
serialization, mount, unmount, archive, restore, tombstone, versioning, dependencies,
copy-on-write, rollback, checkpoint, manifest generation.

```
Create A,B,C → Mount A+B+C → Unmount B
Expect: A resident, B archived, C resident
Restore B → Expect: A+B+C byte-identical
```

## 6. Serialization Stability Test

Build `[KERNEL][PROJECT][STATE]` 100 times and require
`Hash(Context_1) = … = Hash(Context_100)`, preferably token-identical too. Test for nondeterminism
from dict ordering, timestamps, UUIDs, whitespace, generated headers, metadata ordering, JSON
formatting. **Dynamic metadata must not appear inside a supposedly cache-stable prefix.**

## 7. Level 1 — Canary Context Tests

Synthetic modules each holding one unique fact (`codename = BLUEBIRD-731`, `port = 48192`,
`parser mode = RECURSIVE-DELTA`, `region = TEST-ZETA-44`). Ask a question needing exactly one
module. Mounted → correct answer. Unmounted → retrieval triggered or unavailability reported per
protocol. Paged back in → correct answer. Proves mount → use → evict → retrieve → use.

## 8. Negative Canary Test

Archived module says `REDFOX-999`; resident verified module says `BLUEBIRD-731`. The model must
answer BLUEBIRD-731 — proving archived context is genuinely excluded rather than merely labelled
while still occupying the prompt.

## 9. Context Isolation Test

`BASE → {Branch A: SECRET_A, Branch B: SECRET_B}`. A must not know B's secret and vice versa. Then
`BASE + verified(A)` must know A's promoted result and still not know B's private state.

## 10. Copy-on-Write Test

`BASE VALUE=10`; fork A→20, B→30; require BASE=10, A=20, B=30. Discard A → BASE=10, B=30. Commit B
→ NEW_BASE=30. Test without an LLM first.

## 11. Tombstone Test

Distil a ~10,000-token failed branch to a compact record with a revisit rule and an archive
pointer. The model should avoid the failed strategy; when the revisit condition is satisfied it
must be able to retrieve the full branch. Tests that tombstones preserve **decision boundaries**,
not merely summaries.

## 12. Level 2 — Prefix Cache Test

`[KERNEL 10K][PROJECT 20K][STATE 10K][WORK 5K]` then swap WORK. Expected reusable prefix 40K,
`CacheReuse ≈ 40/45 = 88.9%`. Compare Drydock's predicted reusable prefix against backend metrics.

## 13. Deliberate Cache-Break Test

Alter one token near the beginning; reuse must collapse after that position. Proves the
instrumentation detects real prefix reuse rather than reporting optimistic estimates.

## 14. Middle-Eviction Test

`[A][B][C][D]`, remove B → measure actual reuse. Compare against cache-aware layout `[A][C][D][B]`
then `[A][C][D][E]`. The second should preserve substantially more reusable prefix. **Validates the
stable → volatile ordering hypothesis.**

## 15. Cache Economics Test

Not just tokens — wall clock and GPU. Per call: `total_prompt_tokens, cached_prompt_tokens,
uncached_prompt_tokens, output_tokens, TTFT, prefill_time, decode_time, GPU_seconds`. A system that
cuts resident context 30% but doubles uncached prefill is not an improvement.

## 16. Level 3 — Selective Recall Test

100 modules, 5 relevant. Baseline mounts all; MCR mounts five. Require identical factual accuracy
while `Tokens_MCR ≪ Tokens_Baseline`.

## 17. Page-Fault Test

Deliberately omit a required module. Expect: missing info → context fault → locate → mount →
resume → correct solution. Record `PageFaultLatency`, `PageFaultSuccessRate`. **A scheduler need not
select perfectly if page faults are reliable and cheap.**

## 18. Level 4 — Dead-End Experiment

Four plausible approaches, three dead ends. Baseline keeps all four trajectories; MCR should end up
holding approach D plus three tombstones. Continue for many further steps and test that the model
avoids retrying A/B/C, retains *why* they failed, does not carry their full traces, can still
retrieve a failed branch, and preserves performance on D.

## 19. Level 5 — Ratchet Integration Test

Fitness `0,3,5,4,7,6,10` → lineage `0→3→5→5→7→7→10`. At 5/10 the accepted code and accepted context
checkpoint together; the 4/10 branch must not pollute the next attempt.
`GitCheckpoint_t ↔ ContextCheckpoint_t` behave as one transaction.

## 20. Context Rollback Test

At 7/10: fork, insert a false hypothesis, modify code, produce 5/10, roll back. After rollback code
and context are both at the 7/10 state and the false hypothesis is absent or tombstoned. **If code
rolls back but knowledge does not, Ratchet can poison itself — release-blocking invariant.**

## 21. Level 6 — Long-Horizon A/B

A append-only · B global compaction · C modular · D cache-aware modular · E cache-aware MCR +
Ratchet. Identical model, sampling, repository, verifier, budget; multiple seeds.

## 22. Do NOT Use Context-Window Exhaustion as the Primary Test

Asking "which system survives past 128K" mostly tests compaction. Instead impose a smaller working
set: `C_model = 128K` but `C_MCR = 24K`, and ask whether a 24K managed working set matches a 100K
append-only context.

## 23. Context Pressure Sweep

`B ∈ {8K,16K,32K,64K,128K}`; plot `Performance(B)` for baseline vs MCR. **Does modular context
shift the capability curve left?** `MCR @ 32K ≈ Append-only @ 128K` would be a powerful result.

## 24. Long-Horizon Memory Test

Facts planted at T=0,10,50,100; later require each. Compare `Recall(age)` across append-only,
global compaction, MCR. MCR should degrade less with age because age does not determine residency.

## 25. Knowledge Integrity Test

| Information | Expected |
|---|---|
| Immutable constraint | Always retained |
| Verified fact | Retained |
| Temporary hypothesis | Evictable |
| Superseded fact | Replaced/versioned |
| Failed approach | Tombstone |
| Tool documentation | Retrieve on demand |
| Completed-task scratch | Archive |

## 26. Adversarial Context Test

Contradicting modules; archived newer than resident; tombstone referencing a deleted archive;
module exceeding budget; dependency cycle; version change; repeated page faults; double mount; fork
modifying a shared module; rollback during a page fault; verifier crash; inference server restart.
**Fail visibly and recoverably rather than silently corrupting context.**

## 27. Required Metrics

`SolveRate, VerifiedFitness, ResidentTokens, TotalPromptTokens, UncachedPromptTokens, OutputTokens,
CacheHitRate, PageFaultRate, PageFaultSuccess, RetrievalAccuracy, RepeatedDeadEndRate,
ContextRollbackAccuracy, TimeToSolve, GPUSeconds`.

## 28. Two New Metrics

- **CUE** = Relevant Resident Tokens / Total Resident Tokens — how much of the expensive working set
  was actually useful.
- **VPUT** = ΔFitness / Uncached Prefill Tokens — connects MCR to the compute-scaling research.

## 29. Trace Everything

Every inference needs a reproducible context manifest (pinned / ratchet / working / paged-in /
tombstones, with totals, reusable prefix and uncached), plus the result and the actions taken
(COMMIT, NEW, EVICT, ARCHIVE). When something goes wrong you must be able to reconstruct exactly
what the model knew on that call.

## 30. Replay Mode — HIGH PRIORITY

`drydock context replay <call-id>` reconstructs the exact serialized context from a previous
invocation; `--policy append|modular` re-renders it under a different policy. Lets a failure be
asked: *was this a model failure, or did MCR give the model bad context?*

## 31. Differential Context Debugger

`drydock context diff 1841 1842` → unchanged prefix, added, removed, compacted (with before→after
sizes), changed versions, and the token offset where cache invalidation starts.

> For the debugging difficulty being experienced now, this is more valuable than another benchmark.

## 32. Pass/Fail Gates

1. Deterministic context operations pass 100%.
2. Serialization token-identical across repeated builds.
3. Canary retrieval/isolation ≥99%.
4. Context rollback 100%.
5. Predicted prefix reuse matches observed backend behaviour.
6. Page-fault recovery ≥95%.
7. Dead-end/tombstone tests show no significant capability regression.
8. Measurable token/cache efficiency versus baseline.

Only then evaluate real-world solve-rate improvements.

## 33. The First Test To Actually Run

A 20–30 minute synthetic Ratchet task: 20 context modules; 5 permanent facts; 5 temporary
tool/doc modules; 4 deliberately wrong branches; 1 correct branch; incremental tests; ≥3 forced
evictions; 2 forced page faults; 1 forced rollback; 1 module-level compaction; a stable ~20K prefix;
several changing ~5K suffixes.

Run under append-only / modular / cache-aware modular, and emit one report: correctness, final
verifier score, resident tokens, uncached prefill tokens, cache reuse, page faults, successful
restores, dead-end repetitions, rollbacks, total tokens, wall time.

If it fails, the trace shows which subsystem broke. If it passes, it becomes a known-good CI fixture
before moving into noisy real benchmarks.

---

**Priority note (operator):** the biggest addition is not another algorithm — it is **deterministic
replay + context diffing**. With something this stateful, reconstructing exactly what was resident,
what was archived, what changed, and where the prefix broke is essential to telling a context-policy
problem apart from the model making a bad decision.

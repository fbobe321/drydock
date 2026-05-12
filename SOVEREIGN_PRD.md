# Product Requirements Document (PRD)

# Project Name: **Drydock Sovereign AI Stack v2**

### Local Adaptive AI System — Gemma 4 26B + Drydock + GraphRAG + Deep Noir (parallel v2 modules)

---

## Adaptation Notes (vs. upstream PRD draft)

This doc starts from a community PRD draft. Where the draft conflicts with what
Drydock has already committed to or measured, the draft loses. Specifically:

| Upstream draft says                          | What we actually do                                | Why                                                                                                          |
| -------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Runtime: llama.cpp / llama-server (Q3\_K\_M) | vLLM Docker (`vllm/vllm-openai:gemma4`), AWQ-4bit  | Already shipped, 0% timeouts, 70% SWE-bench file match. llama.cpp/Ollama become **portability roadmap**.     |
| Context window: 32 768                       | **131 072** (`--max-model-len 131072`)             | Our hardware (2× RTX 4060 Ti 16GB) handles it with `fp8` KV cache.                                           |
| `temperature: 1.0`, `top_k: 40`              | `temperature: 0.0–0.2` baseline                    | Article advice targets chat/prose. For agentic coding, temp 1.0 *causes* the loops it claims to fix. We only |
|                                              |                                                    | escalate temp to ~1.0 deliberately as a loop-break lever (`agent_loop.py:2032`).                             |
| KV cache `q8_0`                              | `--kv-cache-dtype fp8`                             | vLLM-native equivalent. Different ecosystem (vLLM vs. llama.cpp).                                            |
| Tier 3: Jetson AGX Orin as supported         | **Explicitly out of scope for v2**                 | Per operator rule — Jetson is not to be modified or treated as a deployment target this cycle.               |
| GraphRAG / Deep Noir as faith-based "current" | **First-class roadmap modules**, both ship          | They are deployable v2 capabilities, not contingent on classifier verdict. Classifier sequences engineering attention; it doesn't gate module existence. |
| Cloud-assisted proposer (anywhere)           | **Local-only proposer**                            | Air-gap is the product pitch; cloud proposer contradicts the brand.                                          |

---

# 1. Executive Summary

Drydock Sovereign AI Stack v2 is a fully local, privacy-first AI platform for
secure environments, defense-adjacent customers, regulated enterprises, and
research labs that need frontier-style assistance without sending data to the
cloud.

The system pairs:

- **Gemma 4 26B-A4B-it (AWQ-4bit)** — Google's MoE reasoning model (only ~4B
  active params per token)
- **vLLM** — production-grade inference server (Docker, tensor-parallel)
- **Drydock harness** — orchestration, tool use, loop detection, Admiral
  observer, MetaHarness self-improvement
- **GraphRAG memory** — first-class deployable module; persistent context for
  customer corpora (PDFs, code, manuals, tickets). Roadmap: Phase 2 first cut,
  hardened in Phase 3.
- **Deep Noir activation steering** — first-class deployable module; behavior
  shaping via activation vectors derived from the operator's own research.
  Roadmap: Phase 3 first cut, deepened in Phase 4.
- **Evaluator + classifier loop** — already partially shipped (Admiral + stress
  + autonomous review). The classifier (added in Phase 2) sequences which
  module gets engineering attention next; it does **not** gate whether a module
  ships.

Category claim: **AI that improves after installation.**

### Module posture (read this before reading the rest)

Two questions are kept separate throughout this PRD:

1. **What ships as a deployable customer module?**
   *All three:* harness, GraphRAG, Deep Noir. Defense-adjacent and regulated
   customers ask for these by name; the answer is "yes, here's the module."
2. **Where does engineering attention go next?**
   *Classifier-driven.* The Phase 2 classifier tags each failure as
   harness-class / steering-class / retrieval-class / model-prior /
   ambiguous-input and feeds a prioritized backlog. The classifier sequences
   our work; it does **not** decide whether a module exists.

The product is the **living loop** that ties these together: failures get
classified, the right module gets the fix, the customer's deployment quietly
improves over time.

---

# 2. Product Vision

A sovereign AI system that:

- Runs offline on customer hardware
- Uses local GPUs only
- Protects sensitive data (no telemetry, no remote model calls)
- Learns customer-specific knowledge
- Performs coding and reasoning tasks autonomously
- Self-diagnoses failures (Admiral) and self-improves (MetaHarness, evaluator)
- **Operates with engineered curiosity** — actively detects its own gaps,
  retrieves before asserting, ingests unfamiliar context, and treats
  surprise as signal worth chasing rather than noise to suppress
- Improves continuously without phoning home

---

# 3. Core Problem Statement

## Cloud AI problems

- Data exposure
- Recurring per-seat subscriptions
- Vendor lock-in
- No control over model updates / silent regressions
- Unavailable in air-gapped or classified networks

## DIY local AI problems

- Difficult setup, hardware-specific tuning
- Poor inference performance without serving expertise
- Weak retrieval / no persistent memory
- No orchestration layer (raw chat ≠ agent)
- No self-improvement
- Hard to tune behavior per domain

Drydock Sovereign solves both.

---

# 4. System Architecture

```text
                         ┌────────────────────┐
                         │   Gemma 4 26B-A4B  │  ← MoE, ~4B active/token
                         │   (AWQ-4bit)       │
                         └────────┬───────────┘
                                  │
                         ┌────────▼───────────┐
                         │ vLLM (Docker)      │  ← :8000, fp8 KV cache,
                         │ tensor-parallel 2  │    131K ctx, gemma4 tool parser
                         └────────┬───────────┘
                                  │
                         ┌────────▼───────────┐
                         │  llm_balancer      │  ← :8001, OpenAI-compatible,
                         │  (failover/health) │    cron keepalive
                         └────────┬───────────┘
                                  │
                         ┌────────▼───────────┐
                         │  Drydock Harness   │  ← v2 (TUI) + v3 (clean rewrite)
                         │  • Tool execution  │
                         │  • Loop detection  │
                         │  • Adaptive think  │
                         │  • search_replace  │
                         │  • write_file safety
                         │  • Subagents       │
                         └────────┬───────────┘
                                  │
            ┌─────────────────────┼─────────────────────────┐
            │                     │                         │
   ┌────────▼─────────┐  ┌────────▼────────┐    ┌───────────▼──────────┐
   │ Admiral observer │  │ MetaHarness     │    │ Stress harness +     │
   │ (ship)           │  │ self-improve    │    │ stress_watcher       │
   │                  │  │ (local-only)    │    │ (continuous regression)
   └──────────────────┘  └─────────────────┘    └──────────────────────┘
                                  │
                         ┌────────▼───────────┐
                         │  GraphRAG (Phase 2)│  ← persistent memory module
                         └────────┬───────────┘
                                  │
                         ┌────────▼───────────┐
                         │ Deep Noir (Phase 3)│  ← activation steering module
                         └────────────────────┘
```

---

# 5. Core Components

## 5.1 Base Model Layer

**Gemma 4 26B-A4B-it-AWQ-4bit** (Google, MoE, ~4B active per token).

Used for:

- Coding (primary)
- Reasoning over local repos and documents
- Technical assistance / shell automation
- Document understanding (text)

Operational notes:

- Thinking-token leak (`<|channel>thought<channel|>`) is filtered in
  `providers.py` before display and before message-history storage.
- `tool_choice="auto"`; the gemma4 tool-call parser handles native tool
  decisions.
- Adaptive thinking: OFF for routine writes, HIGH for planning and user
  messages, LOW for error recovery — eliminates 30–120s hangs between file
  writes (see `CLAUDE.md` learnings).

## 5.2 Hosting / Runtime Layer

### Primary runtime: **vLLM (already shipped)**

```bash
# /data3/Models/start_gemma4.sh
docker run -d --gpus all --name gemma4 -p 8000:8000 \
  -v /data3/Models:/models --ipc=host \
  vllm/vllm-openai:gemma4 \
  --model /models/Gemma-4-26B-A4B-it-AWQ-4bit \
  --quantization compressed-tensors \
  --tensor-parallel-size 2 \
  --max-model-len 131072 \
  --max-num-seqs 2 \
  --gpu-memory-utilization 0.95 \
  --kv-cache-dtype fp8 \
  --served-model-name gemma4 \
  --trust-remote-code \
  --tool-call-parser gemma4 \
  --enable-auto-tool-choice \
  --attention-backend TRITON_ATTN
```

| Setting             | Value                  | Why                                              |
| ------------------- | ---------------------- | ------------------------------------------------ |
| Quantization        | AWQ-4bit (compressed-tensors) | Fits 2× RTX 4060 Ti 16GB                  |
| Context             | 131 072                | Coding/RAG headroom                              |
| Tensor parallel     | 2                      | Both GPUs                                        |
| KV cache dtype      | `fp8`                  | vLLM equivalent of `q8_0`                        |
| Tool-call parser    | `gemma4`               | Native function-call decoding                    |
| Attention backend   | `TRITON_ATTN`          | Stable on Ampere/Ada; flash-attn benchmark TBD   |
| Max sequences       | 2                      | Matches 2-GPU serving budget                     |

### Portability runtimes (roadmap, customer-hardware story)

To ship onto customer hardware that prefers other stacks, Drydock must support:

- **llama.cpp / llama-server** — for single-GPU customers (RTX 3090/4090).
  Recommended GGUF: **Q3\_K\_M** (community sweet spot for 24GB cards).
- **Ollama** — easiest first-install path; same Q3\_K\_M.
- **vLLM** — current baseline; multi-GPU, AWQ.
- **Failover** between providers via `llm_balancer.py` (already present).

### Drydock connection spec (customer-tunable)

```yaml
provider: openai_compatible
base_url: http://localhost:8001/v1   # llm_balancer in front of vLLM
model: gemma4
context_window: 131072
temperature: 0.2     # coding default; 1.0 ONLY as a loop-break lever
top_p: 0.95          # vLLM default; do not set top_k unless tuning
```

> ⚠ **Do not** copy the `temperature: 1.0`, `top_k: 40` values from generic
> "run Gemma locally" articles. Those are tuned for chat/prose. Used as the
> default for an agentic coding harness, they cause exactly the loops and
> typos those articles attribute to "the model." Drydock uses temp 1.0
> deliberately as a loop-break escalation, not a baseline.

## 5.3 Drydock Harness Layer

The control plane. Already shipped (v2 TUI + v3 clean rewrite).

**Already shipped:**

- Prompt routing, tool execution, shell automation
- Loop detection (advisory nudges + FORCE_STOP only on pure no-op duplicates)
- `search_replace` quality-of-life: file_path inference, "already applied"
  detection, raw-code fallback
- `write_file` safety: dedup escalation, missing-sibling-import check,
  stub-class anti-pattern detection
- Adaptive thinking (per-call thinking budget)
- Subagent progress streaming
- Hallucinated-tool suppression (`exit_plan_mode`, etc.)
- Auto-AGENTS.md per project (devstral-era; Gemma 4 doesn't strictly need it)

**Planned for this PRD:**

- **Read-before-write enforcement** — block / warn when a tool call would
  modify a file the agent never read in this session. Directly addresses the
  "model leans on internal priors instead of retrieval" failure mode (see §10).
- Pluggable **retriever interface** for GraphRAG (Phase 2 — module ships then)
- **Steering hook** for Deep Noir vectors (Phase 3 — module ships then)
- Failure-class **classifier** signal consumer (routes incoming
  classifier output to harness / GraphRAG / Deep Noir dispatchers)

## 5.4 GraphRAG Memory Layer — Phase 2 first cut, hardened in Phase 3

**Status:** first-class deployable module. Not yet implemented; ships as a
parallel v2 deliverable alongside Deep Noir.

Persistent organisational memory. Customers turn it on by pointing it at their
corpus; it ingests, indexes, and serves grounded retrieval to Drydock.

Sources: PDFs, Office docs, spreadsheets, markdown vaults, source code,
ticketing exports, manuals, research papers.

Pipeline:

```text
ingest → parse → chunk → embed → vector index → graph links → reranker → citation
```

Drydock-side integration:

- Pluggable retriever interface in the harness (so vector-only and
  graph-augmented retrievers swap cleanly)
- Source-priority prompt templates (retrieved evidence pinned above prior)
- Mandatory citation in RAG flows; evaluator verifies citations resolve to
  retrieved chunks
- Read-before-write enforcement (§5.3) makes retrievals actually get used,
  not bypassed in favor of the model's prior

Local-only. No hosted vector DB. Embedding model and reranker run on the same
host as vLLM.

Used when:

- Internal knowledge required
- Context missing in working set
- Cross-session continuity matters

Operational requirement (per §10): retrieval must beat the model's prior.

## 5.5 Deep Noir Activation Steering — Phase 3 first cut, deepened in Phase 4

**Status:** first-class deployable module. Ships as a parallel v2 deliverable
alongside GraphRAG.

Activation-steering vectors derived from the operator's own Deep Noir research
(see project memory). Customers select preset modes per session or per
deployment; Drydock applies the chosen vector via vLLM's logit/activation
hooks (or a sidecar if vLLM upstream support lags).

Targeted use cases:

- **Reduced-hallucination mode** — suppress "make stuff up" directions; pairs
  well with GraphRAG citation enforcement
- **Secure-coding mode** — suppress eval/exec/shell-injection-prone outputs
- **Citation mode** — boost "quote source" directions in RAG flows
- **Legal-precision / analyst / domain-bias presets** — opinionated stacks
  for verticals

Drydock-side integration:

- Steering hook in the harness so the active vector is applied per request
- Per-failure-class routing: if the classifier tags a failure as
  steering-class, Drydock surfaces "try mode X" to the user / queues a vector
  refinement candidate
- Sandbox eval suite: each new vector is regression-tested against a category
  benchmark before promotion

Local-only. No hosted steering service. Vectors ship with the deployment;
retraining / refinement runs on customer hardware (per local-only-proposer
rule).

## 5.6 Evaluator Layer

Already partially shipped — the system already self-observes.

**Already shipped:**

- **Admiral** — live observer that detects skip clusters, RSS bloat,
  raw-markdown leakage, TUI-recycle conditions, and intervenes
- **Stress harness + `stress_watcher.py`** — continuous regression run
  (~1700 prompts), watcher kills leaky TUIs, babysitter relaunches harness
- **`autonomous_review.sh`** — cron-driven Claude review every 30 min
- **`functional_tests.sh` per PRD** + `BASELINE_412.md` — real feature
  tests, no `--help`-only signals

**To ship in Phase 3:**

- Per-task evaluator that classifies failures into:
  ```
  Need memory?      → enqueue GraphRAG ingest
  Need retrieval?   → tune reranker / chunking
  Need tool?        → suggest harness extension
  Need steering?    → flag for Deep Noir
  Need fine-tune?   → recommend LoRA candidate
  ```
- Pareto frontier tracking (accuracy vs. token cost vs. time)
- Execution trace logging per task (Meta-Harness arXiv:2603.28052)

---

## 5.7 Curiosity Layer — engineered intellectual drive

GraphRAG, Deep Noir, and the HLE benchmark together expose a structural
weakness in Gemma 4 and most small local models: they answer from prior
when they should retrieve, plateau on familiar patterns, and treat
"unknown" as something to guess past rather than something to investigate.
The Curiosity Layer is a first-class drydock directive — both a guiding
principle in the system prompt and a concrete subsystem — that converts
that weakness into a feedback loop.

**Operating definition (carries into the system prompt):**

> Curiosity is an inquisitive, often insatiable desire to understand the
> unknown — from everyday questions to scientific discovery. In drydock
> this means: when you notice a gap, you close it; when you encounter
> something unfamiliar, you investigate before asserting; when you have
> spare cycles, you explore the corpus rather than idle.

**Behaviors required of the agent (enforced via prompt + Deep Noir vector
candidate + tool wiring):**

1. **Retrieve before answering on unfamiliar terms.** Any user message
   containing a named entity, paper title, identifier, API, or symbol
   not present in recent context triggers an automatic `retrieve` call
   against GraphRAG before the first content token. Low-confidence
   retrieval triggers a second pass against the arXiv corpus
   (`/data3/arxiv_corpus/graphrag.sqlite`) instead of falling back to
   `web_search`. Fixes the failure mode logged in
   `project_graphrag_underused.md` (Gemma 4 defaults to web_search on
   general-knowledge HLE questions).
2. **Treat surprise as signal, not noise.** When the model's own
   probability estimate disagrees with retrieved evidence, with a tool
   result, or with an HLE judge verdict, the disagreement is logged to
   the curiosity queue (`~/.drydock/dispatch/curiosity.jsonl`) and
   surfaces in the next overnight `autonomous_review` tick as a
   candidate for: GraphRAG ingest, prompt refinement, Deep Noir vector
   training data, or LoRA candidate.
3. **Investigate before asking.** When the user request is
   under-specified, the agent first reads the project (Glob + Read on
   plausible files, `retrieve` against GraphRAG) and proposes a
   concrete interpretation. `ask_user_question` only fires after at
   least one investigation pass — never as a first move on ambiguity.
4. **Explore on idle.** When drydock is idle for > N minutes and the
   classifier dispatch queues are non-empty, the agent runs a single
   exploratory cycle: pick the highest-priority pattern, ingest fresh
   logs into GraphRAG, generate one hypothesis, log to
   `~/.drydock/curiosity_log.md`. Bounded by a tokens-per-day budget
   so curiosity does not starve user work.
5. **Open mindset on conflict.** When new evidence contradicts a
   previously asserted answer, the agent prefers the new evidence and
   issues a correction in the same turn. Extends the existing
   "Retrieval vs. Prior" guidance in §10 into an active rule.

**Curiosity subsystem deliverables (Phase 3, alongside Deep Noir):**

- `drydock.curiosity` module — gap detector, surprise scorer, dispatch
  writer. Reuses classifier infrastructure from §5.6.
- `~/.drydock/dispatch/curiosity.jsonl` queue + consumer wired into
  `autonomous_review.sh`.
- System-prompt section embedding the five behaviors above, kept short
  enough for Gemma 4's 20-line prompt budget (`gemma4.md`).
- Deep Noir vector candidate: "exploration" mode — bias toward retrieval,
  hypothesis enumeration, and reading-before-writing. Trained from
  curiosity-log traces once Deep Noir vector training lands.
- HLE/eval integration: every HLE failure where the judge reasoning
  contains "no answer extracted" or "incorrect prior" is auto-enqueued
  as a curiosity item — the eval becomes a growth signal, not just
  a scoreboard.

**Anti-patterns explicitly forbidden:**

- Curiosity does **not** mean asking the user more questions. The
  default is to investigate, not interrogate.
- Curiosity does **not** override the "advisory-only" rule. Exploration
  cycles never block user work; they consume idle cycles only.
- Curiosity must **not** become rumination — the tokens-per-day budget
  exists specifically to prevent endless exploration loops on a single
  unfamiliar term.

**Acceptance criteria:**

- `retrieve` is called on ≥ 80 % of HLE questions before any content
  token (measured via execution traces, Phase 3).
- `~/.drydock/dispatch/curiosity.jsonl` accumulates ≥ 10 items per
  active week without operator priming.
- `autonomous_review` ships ≥ 1 prompt/AGENTS.md/vector update per week
  that originated from a curiosity-queue item (not a stress-run signal).
- HLE general-knowledge category score lifts measurably vs. the
  May 2026 baseline (1/20 = 5 %, 18/20 "empty"); target ≥ 25 %
  in Phase 3, ≥ 40 % in Phase 4.

**Implementation plan (tiered for ROI per engineering hour):**

### Tier 1 — prompt-level + signal plumbing — *shipped 2026-05-12 (v2.8.20)*

The cheapest, highest-leverage cut. Lands the §5.7 directive as
observable model behavior and starts populating the queue immediately
without waiting on consumer infrastructure.

- ✅ `drydock.curiosity` module — `CuriosityItem` + `CuriosityKind`
  enum, append-only JSONL queue at `~/.drydock/dispatch/curiosity.jsonl`
  with 7-day fingerprint dedup, `detect_gaps()` heuristic extractor for
  unfamiliar-term candidates, `score_surprise()` across three evidence
  kinds (judge_verdict, retrieve, tool_result)
- ✅ Prompt directive in `gemma4.md` — six lines telling the model its
  default posture is "investigate, then assert": first tool call on a
  named-thing user message is `retrieve`, prefer evidence on conflict,
  investigate before asking for clarification
- ✅ HLE → curiosity feedback loop — `scripts/hle_eval.py` enqueues a
  `CuriosityItem` on every NO outcome (kind=`HLE_FAILURE`, high
  confidence when `method=empty` — the GraphRAG-underused failure
  mode), so every eval run produces ≥10 learning signals
- ✅ 26 unit tests in `tests/test_curiosity.py` added to the deploy gate

### Tier 2 — consumer side + forcing function — *next week*

The prompt rule alone gets the model to retrieve more, but Gemma 4 will
still default to prior under context pressure (per
`project_graphrag_underused.md`). Tier 2 closes that gap and starts
shipping fixes from the queue.

- ▢ `agent_loop.py` first-turn hook — call `detect_gaps(user_msg)`
  before the first LLM call; if any candidates returned, automatically
  invoke `retrieve` on the top 2–3 and inject results onto the user
  message as `[CURIOSITY PREFETCH]` context. Forces evidence into the
  window before the model can answer from prior. ~40 lines.
- ▢ `autonomous_review.sh` curiosity consumer — read
  `~/.drydock/dispatch/curiosity.jsonl`, pick highest-confidence
  unconsumed item, generate a fix proposal (AGENTS.md hint / prompt
  rule / GraphRAG ingest plan), commit with subject prefix
  `addresses curiosity:<fingerprint>:`. Mirrors the existing harness
  bucket consumer. ~80 lines.
- ▢ Surprise-on-tool-result scoring inside `_handle_tool_response` —
  when `score_surprise()` exceeds threshold, enqueue an
  `EVIDENCE_CONFLICT` item automatically. Catches confident-but-wrong
  claims without operator priming.
- ▢ HLE re-run with the tier-1 prompt to measure adherence — the
  `retrieve called on ≥80% of HLE Q` criterion needs an actual
  measurement to confirm the prompt rule moves the needle.

### Tier 3 — Phase 3 proper — *after Deep Noir vectors land*

The advanced layer that depends on infrastructure not yet in place.

- ▢ Deep Noir "exploration" mode vector candidate — train from
  curiosity-log traces (retrieve calls, hypothesis enumeration,
  reading-before-writing). Becomes the steerable behavioral prior
  for curiosity once `drydock.steering` has real vectors.
- ▢ Idle-cycle exploration worker — when drydock is idle > N minutes
  and the classifier dispatch queues are non-empty, run one bounded
  exploratory cycle (pick highest-priority pattern, ingest fresh logs,
  generate hypothesis, log to `~/.drydock/curiosity_log.md`). Bounded
  by a tokens-per-day budget so curiosity does not starve user work.
- ▢ Frustration / surprise detection across the full session, not
  just single turns — when the operator repeatedly redirects on the
  same topic, treat it as a high-confidence curiosity signal even if
  no individual turn tripped the per-turn detectors.

---

# 6. Adaptive Failure Logic

```text
If context missing:
   → enqueue GraphRAG ingest (Phase 2) | meanwhile: inject AGENTS.md hints

If no tool available:
   → emit harness extension suggestion to evaluator queue

If behavior poor on a category (e.g., language interpreters):
   → surface worked example in stuck mode (already wired for tree-walking interpreter)
   → Phase 3+: apply Deep Noir vector for that category

If repeated reasoning failures across sessions:
   → recommend LoRA candidate (data captured by execution trace logging)

If loops detected:
   → loop-break sampling (temp+0.3/0.5, freq_pen 0.4–0.7, fresh seed)
   → planner reset + diversity strategy
   → only escalates to FORCE_STOP on pure no-op duplicates

If unfamiliar term / named entity / identifier in user input:
   → mandatory retrieve pass (GraphRAG, then arXiv corpus)
   → web_search only after both local retrievers return low confidence

If model output contradicts retrieved evidence / tool result / judge verdict:
   → log to ~/.drydock/dispatch/curiosity.jsonl
   → autonomous_review picks it up next tick as ingest / prompt / vector candidate

If idle > N minutes AND classifier queues non-empty:
   → one exploratory cycle (bounded by tokens-per-day budget)
   → ingest, hypothesis, curiosity_log entry — never a user-visible action
```

All logic is **advisory-first**. Hard stops are reserved for objectively useless
work (identical-content rewrites). Per operator rule: safety mechanisms must
not block legitimate retries.

---

# 7. Markets

## Primary

- Government secure systems (defense-adjacent first; classified later)
- Defense contractors
- Regulated industries: legal, healthcare, financial services
- Industrial / OT environments
- Engineering teams with sensitive IP
- Research labs requiring data isolation

## Beachhead (per startup direction)

Defense / gov-adjacent vertical. Target a first paid pilot ($5–25K) within
90 days of v2 alpha.

---

# 8. Hardware Targets

| Tier        | Hardware                          | Status                                   |
| ----------- | --------------------------------- | ---------------------------------------- |
| Tier 0 dev  | 2× RTX 4060 Ti 16GB (current)     | ✅ shipped, baseline                     |
| Tier 1      | RTX 3090 / 4090 (single 24GB)     | Roadmap — needs llama.cpp Q3\_K\_M path  |
| Tier 2      | RTX A6000 / RTX 6000 Ada (48GB)   | Roadmap — single-card vLLM AWQ           |
| Tier 3      | Multi-GPU server rack             | Roadmap — tensor-parallel vLLM           |
| **Out of scope** | Jetson AGX Orin              | Explicitly excluded this cycle           |

---

# 9. Key Use Cases

### Secure coding assistant (current strength)

- Local repo coding, bug fixing, refactors
- Shell automation with allowlist/denylist enforcement
- PRD-driven project building (370-PRD test suite)

### Internal knowledge AI (Phase 2)

- Policy lookup, contract review, manual Q&A
- Cross-document reasoning over GraphRAG corpus

### Research copilot (Phase 2+)

- Paper summarisation and method comparison
- Drafting with grounded citations

### Ops assistant (current)

- Script generation, log triage, workflow automation
- Already proven by `autonomous_review.sh` running on Drydock itself

---

# 10. Gemma 4 Operational Warning — Retrieval vs. Prior

Confirmed in real-world deployments and consistent with Drydock's own failure
modes (stub-class anti-pattern, missing-sibling-imports, "writes a function
without reading the file"): **Gemma 4 leans on its training prior even when
ground truth is in retrieved context.**

Drydock must enforce:

1. **Retrieval-first prompts** — "answer using the provided context; if the
   context is insufficient, say so." (system-prompt update)
2. **Mandatory citations** in RAG flows — answer must reference a chunk id
3. **Read-before-write enforcement** in the harness — if `write_file` /
   `search_replace` targets an existing file the agent never read in this
   session, return an advisory and inject a `read_file` nudge before
   permitting the write
4. **Evaluator grounding checks** — sample answers and verify retrieved
   chunks were actually used (token overlap or citation presence)
5. **Source-priority templates** — RAG prompt templates that pin retrieved
   evidence above the model's prior

The read-before-write check is the highest-value low-risk item; it directly
mitigates several of the failure classes already in `MODEL_SHORTCOMINGS.md`.

---

# 11. Roadmap

### Phase 1 — 30 days (Foundation + perf floor)

- ✅ Gemma 4 local hosting (vLLM Docker)
- ✅ Drydock harness (v2 TUI + v3 rewrite)
- ✅ Coding workflows + 370-PRD benchmark
- ✅ Admiral, stress harness, autonomous review
- ✅ **Hosting performance sweep** — clean baseline captured 2026-05-02
  (44–62 tok/s e2e, `perf_results/baseline_1777732278.json`)
- ✅ **Manual failure triage** — `TRIAGE_v1.md`, 11 patterns mapped,
  GraphRAG + Deep Noir justified by data
- ✅ **Read-before-write enforcement** — shipped in `write_file.py` +
  `search_replace.py` advisory paths (already in v2.7.30+)
- ✅ **Install auto-detect** — fresh installs detect local vLLM/Ollama/
  llama.cpp/LM Studio and skip the Mistral API-key prompt (v2.7.34)
- ▢ Customer-hardware portability bring-up (llama.cpp + Ollama backends
  beyond detection — full provider configs)

### Phase 2 — 60 days (Classifier + GraphRAG)  *— shipping live as of v2.7.36*

- ✅ **Failure classifier** — `drydock.core.classifier`, rule-based v0
  (LLM swap-in is v1). 5-bucket taxonomy: harness / retrieval / steering /
  model_prior / ambiguous_input. CLI + `--dispatch` flag.
- ✅ **Per-bucket Dispatcher** — routes signals to
  `~/.drydock/dispatch/<bucket>.jsonl` queues, dedup, error isolation,
  pluggable handlers
- ✅ **Periodic pulse** — `*/10` cron classifies + dispatches recent log
  activity; live pulse produced 211 signals (210 harness, 1 retrieval —
  matches predicted distribution exactly)
- ✅ **GraphRAG first cut** — `drydock.graphrag`, AST symbol indexer
  with cross-package alias resolution + TF-IDF text retriever + SQLite
  storage + CLI (v2.7.34)
- ✅ **Retriever interface in Drydock** — `retrieve` builtin tool,
  auto-discovered, auto-ingest on first call when cwd looks like a project
- ✅ `/graphrag` slash command (stats / ingest / query)
- ✅ autonomous_review consumes dispatch queue as primary signal
- ▢ Embeddings backend (sentence-transformers) behind same Retriever protocol
- ▢ Citation-mode prompt templates + grounding eval

### Phase 3 — 90 days (Deep Noir + self-improvement)  *— scaffolding shipping as of v2.7.36*

- ✅ **Deep Noir scaffolding** — `drydock.steering`, vector format
  (.npy + .toml manifest with sha256), registry, SteeringConfig, three
  applier implementations (Null/LogOnly/sidecar-stub), sandbox eval
- ✅ **Steering hook in agent_loop** — env-gated, log-only by default,
  zero behavior change until DRYDOCK_STEERING_MODES is set
- ✅ `/steering` slash command (status / on / off / list)
- ▢ Real vLLM sidecar applier — drops in as a single class behind the
  existing SteeringApplier protocol when vectors arrive
- ▢ Initial vectors (operator's Deep Noir research output)
- ▢ Per-vector sandbox eval gating — auto-promote if zero regressions on
  the regression suite
- ▢ Execution trace logging (full prompts + tool calls per task)
- ▢ First paid pilot ($5–25K, defense-adjacent)

### Phase 4 — 120 days (Hardening + verticals)

- ▢ Deep Noir vector deepening — additional vectors driven by classifier
  evidence (legal-precision, analyst, per-vertical bias presets)
- ▢ GraphRAG hardening — graph-augmented retrieval, hybrid reranker,
  ingestion freshness signals
- ▢ Domain bundles per vertical (secure-coding, legal-precision, analyst)
- ▢ Measured improvements on per-domain benchmarks
- ▢ Appliance image (factory-trusted, signed updates, audit logs)

---

# 12. Metrics

| Metric                                | Target | Baseline                   |
| ------------------------------------- | ------ | -------------------------- |
| Coding success (functional\_tests)    | +25 %  | 94 % on 412-suite (current)|
| Hallucinations (RAG citation miss)    | −40 %  | TBD — establish in Phase 2 |
| Repeat failures across sessions       | −50 %  | TBD — needs trace logging  |
| Retrieval relevance (P@5)             | +60 %  | TBD — Phase 2 baseline     |
| Customer cloud dependency             | −70 %  | Brand promise, qualitative |
| Task completion rate (interactive)    | +35 %  | Current shakedown baseline |
| SWE-bench file match                  | maintain ≥70 % | 70 % current        |
| `retrieve` calls before content (HLE) | ≥80 %  | <5 % observed May 2026     |
| Curiosity-queue items shipped / week  | ≥1     | 0 — queue not built yet    |
| HLE general-knowledge score           | ≥25 % Phase 3, ≥40 % Phase 4 | 5 % (1/20 May 2026) |

Metrics with "TBD" baselines are explicitly required to be **measured** before
Phase 2 begins, not estimated.

---

# 13. Risks

| Risk                                          | Mitigation                                          |
| --------------------------------------------- | --------------------------------------------------- |
| Local model weaker than frontier cloud        | Harness compensates; pick tasks where local wins    |
| Poor retrieval quality                        | Reranker, chunking discipline, evaluator gating     |
| Steering instability (Deep Noir)              | Sandbox eval, per-domain regression suite           |
| Hardware fragmentation                        | Preset configs per Tier; portability roadmap above  |
| Model regression from upstream releases       | Pin model + image; gate updates through stress run  |
| DIY copycats (open weights + Ollama)          | Moat is harness + memory + steering, not weights    |
| Cron clobbering active edits                  | `.pause_auto_release` and `.pause_watchdog` flags   |
| Cloud creep in proposer / evaluator           | Hard architectural rule: local-only proposer        |

---

# 14. Business Model

- **Software license** — install on customer hardware, per-seat or per-node
- **Appliance** — prebuilt secure workstation (Tier 1/2 hardware, image
  preloaded, factory-trusted)
- **Enterprise support** — annual maintenance, signed updates
- **Premium secure deployments** — government / classified pricing, on-prem
  build/audit support
- **Open core** — harness + agent loop OSS, evaluator/steering/retrieval
  bundles commercial

---

# 15. Strategic Moat

Not the model weights (open). Not the quantization (commodity). Not the
harness alone (replicable).

The compound moat:

- Self-improvement logic (evaluator + autonomous review + stress)
- Memory systems (GraphRAG + retrieval discipline)
- Adaptive routing (per-failure-class actions)
- Activation steering (Deep Noir, derived from operator's own research)
- Secure deployment expertise (air-gap, signed updates, audit logs)
- Polished local UX (TUI today, appliance tomorrow)

---

# 16. Final Thesis

Open models are commodities.
Inference hosting is commoditizing.
RAG is commoditizing.

What stays valuable:

> **A local AI system that knows why it failed and fixes itself.**

That is Drydock Sovereign AI Stack v2.

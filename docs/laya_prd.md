# PRD — Laya System-1 Decision Plane for Drydock (Drydock Reflex)

- **Product:** Drydock
- **Subsystem:** Handshake / Ratchet
- **Working name:** Drydock Reflex
- **Status:** Proposed (Phase 1 core shipped — see §0)
- **Priority:** High
- **Related:** ABC (`docs/abc_prd.md`), MCR (`docs/mcr_validation_prd.md`), Ratchet

> Separate PRD from Adaptive Budgeting, but explicitly: **Laya is the fast decision engine
> Handshake uses to implement ABC.** ABC decides *that* a resource should change and *by how
> much*; Laya decides *which* resource, cheaply, without a generative call.

---

## 0. GROUNDING — what already exists, and what shipped

Laya is the natural completion of the ABC loop: ABC's targeted escalation (§8 there) needs a
cheap answer to "which axis is limiting progress?" Today that would cost a generative call.
Laya makes it a fast System-1 decision behind an interface.

| PRD concept | Existing home / new |
|---|---|
| Provider abstraction, OpenAI-compatible endpoints (§5/§6) | `providers.py` (base_url, urllib probes, injectable transport style from `capacity.py`) |
| Progress / plateau signal feeding ControlState (§7/§14) | `progress.py` `ProgressTracker`, `ratchet.py` verified score |
| Doom-loop / strategy-family fingerprints (§15) | `loop_detect.py` (action sigs), `eratchet.py` (strategy operators) |
| Agent fan-out decisions (§9 agent strategy) | `capacity.swarm_size` |
| Resource-limiting verdict → escalation (§11) | **new** `decision.py` `RESOURCE_TO_AXIS` → `adaptive_budget` axis |
| Config-selected provider + fallback (§18/§19) | **new** `decision.py` factory + `FallbackDecisionProvider` |
| Decision trace state→decision→outcome (§20) | **new** `decision.py` `DecisionTrace`; complements `predictions.py` |

**Shipped (Phase 1, §22):** `drydock/decision.py` — `DecisionProvider` interface,
`ControlState`, `HeuristicProvider` (the §18 fallback and a genuinely useful default),
`LayaProvider` (HTTP client, transport injected for testing), confidence router (§16),
`FallbackDecisionProvider` (§18), `DecisionTrace` (§20), the factory (§19), and the three
Phase-1 decisions (CONTINUE?, WHAT NEXT?, HOW MUCH REASONING?) plus `decide_limiting_resource`
for the ABC coupling (§11). Wired into ABC: `RatchetBudgetAdvisor(decider=…)` installs an
axis-selector so a **confident** Laya/heuristic verdict redirects escalation, while a
low-confidence one defers to ABC's deterministic ladder (the guaranteed floor).

**Deviations from the sketch (deliberate):**
- **Sync, not async.** Drydock's provider layer is sync and these decisions are fast/local; a
  sync interface is testable without an event loop and matches `capacity.py`. Revisit if a
  remote Laya needs concurrency.
- **No Laya model exists yet.** `LayaProvider` is a client to a `provider: laya` endpoint; with
  no server it raises `DecisionUnavailable` and the chain falls back to the heuristic. So Laya
  is off by default and never a single point of failure (§18), exactly as required.

**Constraints honored:** local fleet only, no Anthropic API, no phone-home (the endpoint is the
user's own server, like every other provider). Laya may *recommend* completion but the verifier
is authoritative and cannot be overridden (§9/§17). Advisory by default.

**Remaining:** an actual Laya/Jev service; an `LLMDecisionProvider` (§16 escalation to the
primary LLM on low confidence); Phases 2–4 (Ratchet wiring beyond ABC, context routing, swarm
control); the §23 four-arm experiment (validated hands-on through the TUI).

---

## 1. Executive Summary

Drydock relies primarily on generative LLMs both to perform work and to decide what work
happens next. That is inefficient: large autoregressive models are expensive and slow when the
operation is simply "continue or stop?", "which of five actions next?", "did this make
progress?", "spend more reasoning?", "which module to load?", "spawn another agent?".

Introduce a dedicated **System-1 Decision Plane** using **Laya**, a small decision-oriented
model exposed as an independent local service. Laya does not replace Drydock's coding/reasoning
models: **Laya decides. Generative models work. Ratchet measures. Handshake orchestrates.** The
result is a heterogeneous inference architecture where different model shapes do the jobs they
are best suited to.

## 2. Product Thesis

Current harnesses use one expensive autoregressive model for both cognition and orchestration.
Drydock separates them: Handshake schedules → **Laya** (decision plane) routes/budgets/decides
→ coding / reasoning / search workers execute → tools → Ratchet measures → back to Laya. Laya
is a fast reflex; larger models provide expensive deliberative cognition.

## 3. Goals

Host Laya independently of generative models; use it for frequent low-cost orchestration; a
model-independent `DecisionProvider` interface; Laya controls reasoning-budget allocation,
context routing, agent fan-out, and stagnation detection; decisions integrate with Ratchet's
verified progress; safe fallback when Laya is unavailable/uncertain; every decision+outcome
recorded for evaluation/training. **Overarching: spend generative inference on generating and
reasoning — not on routine orchestration.**

## 4. Non-Goals

Laya will not initially write code, edit files, generate long plans, replace the primary LLM,
replace Ratchet verification, execute shell commands, or receive the whole repo/conversation.
**Laya is a decision service, not another worker agent.**

## 5. Deployment Architecture

Laya runs as a separate process/service (e.g. `:8000` Laya, `:8001` coding model, `:8002`
reasoning model). Physical placement is irrelevant — Drydock reaches all of them through
provider abstractions (`providers.py` conventions). `LayaProvider` is an OpenAI-style HTTP
client with an injectable transport; any failure → `DecisionUnavailable`.

## 6. DecisionProvider Interface

Model-independent seam Handshake/ABC depend on — **the interface, not Laya, is load-bearing**,
so `provider: heuristic` / `provider: jev` requires no changes elsewhere. Primitives:
`boolean(state, question)`, `choose(state, question, choices)`, `score(state, criterion)`.
Named decisions are built on top so a new provider implements only the primitives.
Implementations: `LayaProvider`, (future `JevProvider`, `LLMDecisionProvider`),
`HeuristicProvider`.

## 7. The Control State

Laya receives a compact `ControlState`, not the working context: objective, fitness/maximum,
previous fitness, plateau iterations, strategy family + attempts, last action, active/available
modules, resources (reasoning/agents/iterations), available actions, hypotheses. Drydock's
cognitive dashboard.

## 8. State Compression

`ControlState` stays small (**~200–500 tokens**). This draws the boundary between **Working
Context** (large, for generative models) and **Control Context** (small, structured, to control
inference). `ControlState.to_prompt()` renders the terse form.

## 9. Core Decision Types

Six classes: **Continue** (yes/no), **Action Selection** (continue / reason_deeper /
retrieve_context / run_tool / spawn_agents / change_strategy / finish), **Reasoning Budget**
(minimal…maximum), **Context Routing** (which module, dynamic choices), **Agent Strategy**
(single / N hypothesis agents / critic / research), **Termination** (recommend only — Ratchet/
verifiers remain authoritative; Laya cannot override a failing verifier).

## 10. Batched Decisions

Handshake asks several questions at once (`batch`) and gets a probabilistic picture rather than
prose to parse. (Phase-1 wires the three single decisions; batching is a thin loop over them and
lands with a real Laya endpoint that supports it.)

## 11. Integration with Adaptive Budgeting

Instead of `if failure_count > 2: reasoning = HIGH`, Handshake asks *"what resource is most
likely limiting progress?"* over `{more_reasoning, more_context, more_agents, more_tools,
different_strategy, none}`. `decide_limiting_resource` returns that verdict; `RESOURCE_TO_AXIS`
maps it to an `AdaptiveBudgetController` axis. A confident verdict redirects ABC's escalation; a
low-confidence one defers to ABC's fixed ladder. This is the concrete ABC↔Laya coupling.

## 12. Integration with Modular Context

Laya joins the context router: when a worker stalls, ask which *missing* module is most
relevant and mount just that one — no global context expansion. (Phase 3; the `choose` +
`_score_module` seam exists.)

## 13. Context Eviction

The reverse: ask which active module is least relevant and unload it — genuine context paging,
pairing with MCR's module-unload lever (MCR §0 found unload, not tool-result paging, is the real
knob).

## 14. Ratchet Integration

Ratchet stays the objective measurement layer: improvement → checkpoint → `ControlState.progress
= strong` → Laya likely says continue; a plateau (`40→40→40`) → Laya decides the resource/
strategy change. Laya consumes Ratchet's verified signal; it never fabricates progress.

## 15. Doom-Loop Detection

Maintain **semantic action fingerprints** (`loop_detect.py` for exact repeats; strategy
*families* for near-duplicates — "increase mutex scope" ≈ "lock larger section"). `ControlState`
encodes `strategy_family` + `family_attempts` + `fitness_gain`. A strong "don't continue this
family" verdict triggers **strategy invalidation** (record "avoid lock_expansion: 3 attempts /
0 gain"); the compact lesson stays resident while the full histories leave active context — a
direct MCR tie-in.

## 16. Decision Confidence

Decisions are not commands. `confidence ≥ 0.80` → execute; `0.55–0.80` → blend with heuristics;
`< 0.55` → escalate (to the primary LLM once `LLMDecisionProvider` exists). Expensive
decision-making is used only when the cheap system is uncertain. (`Decision.actionable` /
`.uncertain`, `EXECUTE_THRESHOLD` / `FALLBACK_THRESHOLD`.)

## 17. Safety Hierarchy

Explicit and enforced: **Hard constraints → Verifier/Ratchet → Policy/resource limits →
Handshake → Laya → Worker preference.** Laya cannot decide "ignore failing tests and finish"
(the verifier wins) nor exceed user resource ceilings. In code: the termination heuristic keys
off `state.solved` (a verifier fact), and ABC's actuation leaves the effort governor free to
force LOW.

## 18. Failure Handling

Laya down → `HeuristicProvider` → (future) `LLMDecisionProvider`. The task continues. Laya is an
accelerator, never a single point of failure. `FallbackDecisionProvider` always returns an
answer, with `HeuristicProvider` last.

## 19. Configuration

```yaml
decision_plane:
  enabled: true
  provider: laya
  endpoint: http://localhost:8000/decide
  fallback: [heuristic, primary_llm]
  confidence: {execute: 0.80, fallback: 0.55}
  decisions: {routing: true, reasoning_budget: true, context: true, agents: true, termination: true}
  logging: true
```

`provider: heuristic` (or `jev`) requires no other changes. `decision_provider(config)` builds
the chain; absent/disabled → heuristic only.

## 20. Decision Trace

Every decision → a structured record: `decision_id`, `state_hash`, question, options, selected,
action, outcome (fitness before/after), cost (latency). Drydock accumulates a dataset of
**state → decision → outcome**, not merely prompt → response. `DecisionTrace` appends JSONL;
never raises.

## 21. Future Learned Scheduler

Enough runs of `ControlState + Decision + Outcome + ComputeCost` become training data to
optimize `Utility = TaskProgress − λ·ComputeCost`, i.e. `a* = argmax_a E[ΔFitness(a) − λ·C(a)]`:
choose the next action expected to produce the most verified progress per unit compute. A far
more interesting objective than "LLM, what next?". Pairs with `predictions.py` calibration.

## 22. MVP

- **Phase 1 (SHIPPED):** `DecisionProvider`, `ControlState`, `LayaProvider`, decision logging,
  fallback provider; three decisions (CONTINUE?, WHAT NEXT?, HOW MUCH REASONING?).
- **Phase 2:** connect Ratchet (progress / plateau / regression); test whether Laya
  distinguishes continue / escalate / change-strategy.
- **Phase 3:** modular-context routing (LOAD / UNLOAD / COMPACT / RETRIEVE).
- **Phase 4:** swarm control (single / specialist / critic / fan-out / fan-in). Laya then owns
  most routine Handshake scheduling.

## 23. Required Experiments

Same TerminalBench tasks (tbench-2 only, through the real TUI) under: **A** generative-LLM
orchestration, **B** heuristic orchestration, **C** Laya orchestration, **D** Laya + LLM
fallback. Measure: success rate, total tokens, GPU-seconds, wall time, decision latency, tool
calls, repeated-strategy rate, context tokens, agent executions, ratchet improvements.
**Hypothesis worth testing: D is the interesting architecture** — cheap System-1 decisions
almost everywhere, expensive System-2 orchestration only when uncertainty warrants.

## 24. Architectural End State

Drydock becomes a heterogeneous **inference runtime**: Handshake schedules; Laya is the
System-1 reflex driving the reasoning-budget / context-router / agent-router; generative models
are System-2 workers; tools compute what machines compute directly; modular context is memory;
Ratchet is objective feedback. The through-line of everything built so far:
**Ratchet → Handshake → modular context → adaptive compute → heterogeneous cognitive routing.**
We stop assuming one autoregressive LLM should perform every cognitive function.

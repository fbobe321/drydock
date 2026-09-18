# Product Requirements Document
# Drydock Compute-Optimal Agent Scaling Research

**Working research title:** Compute-Optimal Cumulative Agentic Search: Scaling Laws for Verifier-Guided Software Engineering Agents

- **System:** Drydock
- **Components:** Ratchet, ERatchet, Swarm, GitCheckpoint, verifier/test infrastructure
- **Status:** Research PRD
- **Primary objective:** Determine how a fixed inference-compute budget should be allocated among parallel agents, cumulative generations, diversity, and verification to maximize software-engineering task performance.

---

## 0. Drydock Methodology Constraint (compliance — READ FIRST)

This PRD is authored under two standing Drydock rules that constrain **how** the research may be executed (not what it studies):

1. **No custom eval harness (HARD BAN).** Drydock does not build judge pipelines or automated batch/trial runners — *"even pexpect-driven TUI ones."* Model-facing evaluation happens by **using Drydock**, driven through the real TUI, not a headless sweeper.
2. **TUI-driven.** Swarm/ratchet runs are driven hands-on through the real `/swarm`, `/ratchet`, `/eratchet` commands (tmux send-keys + capture-pane), like an operator — not via programmatic `run_swarm`/`run_eratchet` drivers or `-p`.

**What this means per phase:**
- **Compliant to build directly:** Phase 1 instrumentation/telemetry (§20), lineage graph (§21), QD archive (§12), crossover (§13), graded-fitness/diversity/correlation metrics (§9–§11), and the **Phase-7 Adaptive Swarm Controller** (§14, Output 4) — that controller is a *shipped Drydock feature the operator runs*, i.e. "using Drydock," not a harness.
- **Collides with the ban → do NOT build as an automated runner:** Phase 2's "experiment runner that automates repeated experiments" and §19's automated 10–30 trials/config.
- **Compliant execution of the breadth/depth matrix (§7):** instrument first, then **hand-drive `/eratchet` and `/swarm` through the TUI** per (N,G) config; telemetry lands in `metrics.json`; the scaling-law fit (§17) is done **offline** from those records. Trials are operator-driven runs, not a judge pipeline.

The scaling-law *analysis* (curve fitting, response surfaces) is offline post-processing of telemetry Drydock emits during real use — that is not a runtime eval harness and is fine.

**Relationship to existing docs:** this PRD supersedes and expands the swarm PRD's `docs/multi_agent_swarm_prd.md` §41 ("Swarm-Width Scaling Law / Productive-N") — §41 is the seed; this is the full program. It also builds on §33 (Marginal Agent Utility) and §34 (Benchmarking, incl. the same TUI-driven constraint).

**Architectural position (operator, 2026-09-18) — this PRD sits ON TOP of the Modular Context
Runtime.** The layering is:

> **Modular Context Runtime → Ratchet → Compute/Context Governor → parallel execution/agents**

See `docs/modular_context_runtime_prd.md` (MCR). MCR makes *context* the primitive and agents one
execution topology over it. Consequently this PRD's `(N*, G*, D*)` is best understood as a
**projection onto the agent axis** of a larger allocation problem that also includes `B` (resident
context budget) and `M` (model selection): `max Verified Progress / Inference Compute`. The
breadth/depth experiments below remain valid and should be run — they measure one axis of that
surface — but the adaptive controller (Phase 7 / Output 4) should ultimately be the *unified*
compute/context governor described in MCR §28 phase 8, not an agent-count-only controller.

---

## 1. Executive Summary

LLM agent systems increasingly improve performance by spending additional inference-time compute. That compute can be allocated in several fundamentally different ways:

- More parallel agents
- Longer individual trajectories
- More sequential generations
- Independent retries
- Iterative revision
- Selection
- Verification
- Evolution/crossover
- Diverse agent roles or strategies

There is not yet a generally accepted equivalent of the Chinchilla scaling law that answers:

> Given a software-engineering problem and a fixed inference-compute budget, how many agents should be used, how deeply should they iterate, and how should those agents interact?

Drydock provides an unusual platform for investigating this question because **Ratchet** and **ERatchet** already implement cumulative verified selection. Ratchet serially preserves verified improvements. ERatchet expands this into parallel evolutionary search using multiple candidate workspaces, selection, diversity preservation, and crossover.

This research systematically varies the allocation of inference compute across `N` (parallel agents), `G` (cumulative generations), and `D` (agent/search diversity) while controlling total compute `C`.

**Primary research question:** For a fixed inference-compute budget, what combination of breadth, cumulative depth, and diversity maximizes the probability of solving a software-engineering task?

**Long-term product goal:** convert the resulting empirical scaling relationships into an **Adaptive Swarm Controller** that automatically determines how much inference compute Drydock should spend and how it should spend it.

---

## 2. Motivation

Traditional agent retries often discard useful intermediate progress: an agent attempts a task, fails, retries, and may regress on portions it previously solved.

Ratchet changes this. A verifier evaluates the workspace after each attempt. If fitness improves, the workspace is checkpointed; if not, Drydock restores the previous best state. Therefore:

`F_{t+1} ≥ F_t`

where `F_t` is the best verified fitness achieved by generation `t`. The search trajectory becomes monotonically non-decreasing with respect to the verifier.

Example — raw attempts: `5 → 8 → 6 → 11 → 9 → 15`. Ratchet lineage: `5 → 8 → 8 → 11 → 11 → 15`.

ERatchet extends this by producing multiple candidates from the current population and selecting useful descendants — enabling study of agent scaling under **cumulative selection**, rather than simply best-of-N sampling.

---

## 3. Research Hypothesis

**Central hypothesis:** There exists a compute-optimal allocation between parallel exploration and cumulative exploitation for agentic software-engineering tasks.

Increasing agent count should initially increase the probability of discovering useful modifications; marginal gains should eventually decline as agents generate correlated solutions. Allocating all compute to a single long trajectory may under-explore. Therefore an intermediate allocation may outperform either extreme.

`(N*, G*, D*) = argmax_{N,G,D} P(solve | C)`

subject to `C ≈ N × G × C_attempt` for the simplest controlled experiment.

---

## 4. Secondary Hypotheses

- **H1 — Diminishing returns from agent count.** Increasing `N` improves performance initially but yields diminishing marginal returns; `P_solve(N) → saturation`. The saturation point depends on task difficulty and agent diversity.
- **H2 — Cumulative selection improves compute efficiency.** For equal compute, `Ratchet > Independent Retry` on tasks with independently improvable subproblems; the advantage grows with task horizon.
- **H3 — Hybrid breadth × depth beats either extreme.** At equal compute, `8 agents × 8 generations` may beat both `64 × 1` and `1 × 64` for appropriate problem classes. The optimum is discovered experimentally, not assumed.
- **H4 — Diversity matters more than raw agent count.** Effective search capacity depends on candidate independence, not agent count. Define `N_effective ≤ N`; `P_solve = f(N_effective)` may explain performance better than raw `N`.
- **H5 — Verifier granularity enables better scaling.** Binary verification gives `F ∈ {0,1}`; graded verification gives `F = passing/total`. Graded fitness should enable more efficient cumulative search because partial improvements become selectable.
- **H6 — Crossover can exploit complementary partial solutions.** Candidate A `14/22` and B `13/22` may pass disjoint checks; crossover may combine them into a superior descendant.
- **H7 — Optimal agent count changes during a task.** `N* = f(search state)` — early search favors breadth, late search favors concentrated exploitation of the strongest lineage.

---

## 5. Research Questions

1. How does solve probability scale with agent count?
2. How does solve probability scale with cumulative generations?
3. For equal compute, what allocation between breadth and depth performs best?
4. At what point do additional agents stop providing meaningful marginal benefit?
5. How strongly correlated are parallel agent trajectories?
6. Does explicit diversity increase effective agent count?
7. Does Ratchet outperform independent retries at equal token budgets?
8. Does ERatchet outperform basic Ratchet?
9. Does quality-diversity preservation outperform winner-take-all selection?
10. Does crossover improve final solve rate?
11. Does verifier granularity affect scaling behavior?
12. Can early measurements predict the useful swarm size for a task?
13. Can Drydock determine the compute-optimal agent count dynamically?
14. Do scaling relationships transfer between models?
15. Do they transfer between different classes of software-engineering tasks?

---

## 6. Experimental Systems

Benchmark at minimum five search architectures.

- **A. Independent Retry** — each attempt begins from the original workspace; no cumulative state. Baseline.
- **B. Best-of-N** — `N` independent agents attempt the problem; verifier selects the best final result; no additional generations.
- **C. Ratchet** — single-lineage cumulative verified search (`N=1, G>1`); improvement → checkpoint, regression → rollback.
- **D. ERatchet** — parallel evolutionary cumulative search (`N>1, G>1`): parallel worktrees, verifier fitness, selection, checkpointing, diversity archive.
- **E. ERatchet + Crossover** — full evolutionary config: population search, cumulative selection, QD archive, complementary-solution detection, crossover, continued mutation/search.

---

## 7. Core Breadth × Depth Experiment

Hold total agent-attempt budget constant. Example `C = 64 attempts`:

| Agents | Generations | Attempts |
|---:|---:|---:|
| 1  | 64 | 64 |
| 2  | 32 | 64 |
| 4  | 16 | 64 |
| 8  | 8  | 64 |
| 16 | 4  | 64 |
| 32 | 2  | 64 |
| 64 | 1  | 64 |

Repeat at multiple budgets `C ∈ {8,16,32,64,128,256}` → a response surface `Performance = f(N,G,C)`.

Attempt count is only the first control variable; the primary analysis should ultimately normalize against **actual inference tokens/compute**, because one attempt may be substantially longer than another.

*(Drydock execution — see §0: each cell is a hand-driven `/eratchet`/`/swarm` TUI run, not an automated sweep.)*

---

## 8. Compute Accounting

Every experiment must record:

- **Model compute:** input tokens, output tokens, cached tokens (when available), total inference tokens, inference duration, tokens/sec, model id, quantization, context length.
- **Hardware compute:** GPU type, GPU count, GPU-seconds, peak VRAM, average VRAM, utilization (when available), energy (when measurable).
- **Search compute:** agent attempts, generations, parallel agents, verifier executions, crossover operations, rollbacks, checkpoints.

Preferred normalized variable: `C_token = total generated inference tokens`, with GPU-seconds as an additional real-world cost metric.

---

## 9. Fitness

Primary graded fitness `F = P/T` (`P` = passing graded checks, `T` = total). Also retain raw `P/T` (newly solved checks are useful for evolutionary analysis).

The research system must never rely solely on the aggregate count internally — it must preserve the **identity** of each passing/failing check:

```
candidate_A:
  pass: [1,2,3,4,5,7]
  fail: [6,8]
candidate_B:
  pass: [1,2,3,4,5,6]
  fail: [7,8]
```

This allows complementary candidates to be detected.

---

## 10. Marginal Compute Return

Per generation: `MCR = ΔF / ΔC`. Units e.g. `new passing tests / 1M inference tokens` or `ΔF / GPU-hour`. One of the most important quantities in the research. If doubling agents `8 → 16` produces negligible improvement (`MCR_{8→16} ≈ 0`), the search has likely entered an agent-count saturation regime.

---

## 11. Diversity Measurement

Raw agent count is insufficient. Measure diversity among candidates via: files modified, lines modified, patch similarity, AST differences, test-pass vectors, approaches described in reasoning summaries, tool-use sequences, error messages encountered, code paths changed.

A simple behavioral representation is the test vector `v_i = [1,1,0,1,0,…]`. Measure mean correlation `ρ` and an experimental effective-agent metric `N_eff = f(N, ρ)`. The functional form should be learned from results, not assumed.

---

## 12. Quality-Diversity Archive

ERatchet must preserve candidates providing novel verified behavior even when aggregate fitness is lower. Example: incumbent `18/22`, candidate `17/22` — normally the candidate loses, but if it uniquely passes checks 20 and 21 it should enter the diversity archive because it contains useful genetic material. Archive criteria consider both **fitness** and **behavioral novelty**.

---

## 13. Crossover Research

Crossover targets complementary verified capabilities. Given `A` = tests passed by A, `B` = tests passed by B, compute complementarity `|A △ B|` (symmetric difference). High-complementarity candidates become crossover candidates. The child is tested normally; **no merged solution is accepted without verifier improvement** — this preserves the Ratchet invariant.

---

## 14. Dynamic Swarm Sizing

The ultimate product feature is not a hard-coded optimal agent count — it is an adaptive controller:

```
Start with small population
        ↓
Generate candidates → Verify → Measure {fitness gain, diversity, correlation, compute cost}
        ↓
Estimate marginal value of more breadth
        ↓
 ┌───────────────────────┐
 │ Expand population?    │
 │ Continue population?  │
 │ Contract population?  │
 │ Increase depth?       │
 │ Trigger crossover?    │
 │ Stop?                 │
 └───────────────────────┘
```

The controller learns to allocate inference compute according to observed search behavior.

---

## 15. Proposed Adaptive Signals

- **Increase agent count when:** candidate diversity remains high; independent improvements continue appearing; multiple distinct test subsets are being solved; marginal compute return remains positive; uncertainty about the best lineage remains high.
- **Decrease agent count when:** patches become highly correlated; agents repeatedly rediscover identical solutions; no new test coverage appears; marginal return approaches zero; one lineage strongly dominates.
- **Increase depth when:** steady incremental verified progress occurs; modifications build naturally on previous changes; remaining failures appear dependent on prior fixes.
- **Trigger crossover when:** multiple archive members contain complementary test-pass vectors.

---

## 16. Task Difficulty

A useful scaling law must account for task difficulty `D`. Candidate predictors: baseline model solve rate, tests initially failing, repository size, files implicated, dependency depth, required tool calls, baseline trajectory length, historical attempts required, error diversity, required context. Do not assume a universal difficulty equation; collect features and determine which predict compute requirements.

---

## 17. Scaling-Law Model

The eventual empirical model may resemble `P_solve = f(C, N, G, D, ρ, V, M)` where `C`=compute, `N`=parallel agents, `G`=cumulative generations, `D`=task difficulty, `ρ`=agent correlation, `V`=verifier characteristics, `M`=model capability.

Optimization target: `max P_solve subject to C ≤ C_budget`. A second objective minimizes expected compute: `min E[C | solve]`. This produces both maximum-performance and compute-efficient operating points.

---

## 18. Benchmark Strategy

Use software-engineering tasks with externally executable verification: Terminal-Bench-style tasks, SWE-bench-style repo repair, unit-test repair, implementation tasks, debugging, refactoring with behavioral tests, multi-file feature additions, build-system repair, command-line environment tasks. Tasks span easy/medium/hard/very-hard regimes. Results must be **stratified by difficulty**, not reported only as aggregate success.

---

## 19. Repetition and Statistical Requirements

Agent behavior is stochastic. Every configuration runs multiple independent trials. Minimum initial target: **10 trials/config**; publication-quality: **30+ trials/config** where compute permits. Record mean, median, variance, confidence intervals, solve probability, compute-to-solve distribution. Retain random seeds.

*(Drydock execution — see §0: trials are operator-driven TUI runs, not an automated batch runner.)*

---

## 20. Required Telemetry

Every agent attempt generates a structured research record. Example:

```json
{
  "experiment_id": "...",
  "task_id": "...",
  "model": "...",
  "generation": 7,
  "agent": 4,
  "parent": "...",
  "tokens_in": 18342,
  "tokens_out": 4921,
  "runtime_seconds": 84.2,
  "fitness_before": 0.545,
  "fitness_after": 0.636,
  "tests_before": "12/22",
  "tests_after": "14/22",
  "new_tests": [15, 17],
  "lost_tests": [],
  "checkpoint": true,
  "rollback": false,
  "archive_added": true
}
```

The schema must support reconstruction of the complete evolutionary lineage.

---

## 21. Lineage Graph

Every candidate knows: `parent, generation, mutation/agent, fitness, checkpoint, children`. For crossover: `parent_A, parent_B`. Enables visualization of the search as an evolutionary tree/DAG:

```
                     5/22
                       │
             ┌─────────┼─────────┐
             │         │         │
            7/22      9/22      6/22
                       │
                 ┌─────┼─────┐
                 │     │     │
               11/22 10/22 13/22
                             │
                      ┌──────┴──────┐
                    15/22         14/22
                      \             /
                       \ crossover /
                            │
                          18/22
                            │
                          22/22
```

This should eventually become a Drydock visualization capability as well as a research artifact.

---

## 22. Ablation Studies

Isolate which components produce improvement. Run ERatchet with individual capabilities disabled: no rollback; no cumulative checkpoint; no diversity archive; no crossover; no graded fitness / binary verification only; homogeneous vs diversity prompts; static vs adaptive population. Establishes causal contribution rather than attributing all performance to the combined system.

---

## 23. Cross-Model Experiments

Repeat selected experiments across model sizes/families. Does a stronger model need fewer agents? Does a weaker model benefit more from breadth? Does a stronger model benefit more from depth? Does diversity compensate for model weakness? Is `N* = f(M)`? Especially important because Drydock is model-agnostic.

---

## 24. Local Hardware Research

Study situations where logical agent count exceeds physically concurrent inference capacity (e.g. logical 32 vs GPU-supported concurrent streams 4). Drydock schedules the population in waves, separating `N_logical` from `N_concurrent`, and tests whether large logical swarms remain valuable on modest hardware. A core Drydock use case.

---

## 25. Stop Conditions

- **Solved:** `P = T`.
- **Compute exhausted:** `C ≥ C_budget`.
- **Search saturation:** no meaningful verified improvement for `K` generations and measured marginal return below threshold.
- **Diversity collapse:** candidates converge to nearly identical behavioral/patch states without improvement.

The last two should initially be **recorded** rather than aggressively enforced until sufficient empirical data exists.

---

## 26. Research Outputs

- **Output 1 — Empirical scaling curves:** `P_solve vs N`, `P_solve vs C`, `P_solve vs G`, `N_effective vs N`, `MCR vs N`.
- **Output 2 — Breadth × Depth scaling surface:** `Performance(N, G | C)` — the central experimental result.
- **Output 3 — Predictive scaling model:** given model / task characteristics / hardware / token budget / verifier → predict recommended logical agents, concurrency, generation depth, diversity strategy, crossover policy, expected compute.
- **Output 4 — Drydock Adaptive Swarm Controller:** e.g. `drydock solve task --compute-budget 10M --adaptive-swarm` — Drydock decides `1×32 / 4×8 / 8×4 / 16×2 / 32×1` and can change allocation while solving.

---

## 27. Success Criteria

- **Research MVP:** statistically meaningful differences among breadth/depth allocations under equal compute.
- **Phase 2:** diversity/correlation measurements predict diminishing returns from additional agents.
- **Phase 3:** predict useful swarm sizes from early task behavior.
- **Phase 4:** adaptive allocation outperforms fixed-N strategies at equal compute.
- **Long-term:** `Adaptive Cumulative Search > Fixed Agent Search` across multiple models, task classes, and compute budgets.

---

## 28. Implementation Phases

- **Phase 1 — Instrumentation.** Add standardized experiment telemetry to Ratchet and ERatchet (tokens, verifier results, lineage, runtime, patches, checkpoints, hardware metrics). Do not substantially alter search behavior yet.
- **Phase 2 — Controlled scaling harness.** *(See §0: in Drydock this is NOT an automated headless/pexpect runner. It is operator-driven TUI runs parameterized by N/G/budget/model/task/seed/strategy, with telemetry collected for offline analysis.)*
- **Phase 3 — Breadth × Depth study.** Run controlled fixed-compute experiments; produce first scaling curves; determine whether an interior optimum exists.
- **Phase 4 — Diversity study.** Introduce deliberate diversification; measure `N` vs `N_effective`; test whether effective diversity predicts performance better than raw population.
- **Phase 5 — Evolutionary study.** Evaluate archive, novelty selection, crossover, complementary-test crossover, multiple-parent lineage vs best-of-N and Ratchet.
- **Phase 6 — Early prediction.** Use the first few generations to estimate difficulty, marginal return, correlation, useful population size; test whether early measurements predict later saturation.
- **Phase 7 — Adaptive controller.** Implement dynamic expand/contract/deepen/diversify/crossover/stop decisions.
- **Phase 8 — Benchmark validation.** Evaluate the adaptive controller vs single agent / independent retry / best-of-N / Ratchet / fixed ERatchet / fixed swarm under matched compute budgets.

---

## 29. Key Research Principle

**Do not optimize for agent count. Optimize for useful independent search per unit compute.** A 32-agent swarm producing near-identical patches may effectively represent only a few independent searches; a smaller population producing genuinely complementary discoveries may have much greater effective search capacity. The ultimate quantity of interest is not `N` but:

> **Useful Search Compute = f(C, N_effective, G, V)**

---

## 30. North-Star Research Question

> Given this model, this software-engineering task, this verifier, this hardware, and this inference-compute budget, what is the compute-optimal way for Drydock to organize its agents?

The desired outcome is the agentic equivalent of a scaling law:

> **(N*, G*, D*, S*) = f(M, T, V, H, C)**

where `S*` is the optimal search strategy. Drydock would no longer require the user to decide whether to run 1, 4, 16, or 64 agents — it would measure the search itself and allocate compute dynamically.

**Ratchet provides cumulative depth. ERatchet provides evolutionary breadth. The research determines the compute-optimal balance between them.**

---

## Prioritization (operator, 2026-09-18)

Prioritize **Phase 1 instrumentation + the 64-attempt breadth/depth matrix (§7)**. That experiment is relatively simple with what Ratchet/ERatchet already have, and it immediately shows whether `1×64, 2×32, 4×16, 8×8, 16×4, 32×2, 64×1` produce meaningfully different outcomes at ~equal inference budgets. That result determines where the deeper scaling-law work should go.

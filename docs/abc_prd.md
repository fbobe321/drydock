# PRD — Adaptive Inference Budgeting for Drydock (ABC / Handshake)

- **Working name:** Handshake Adaptive Budget Controller (ABC)
- **Product:** Drydock / Ratchet / Handshake
- **Status:** Proposed (MVP §20 shipped as pure-logic controller — see §0)
- **Related:** MCR (`docs/mcr_validation_prd.md`), Ratchet, Context Runtime
- **Primary goal:** Dynamically allocate reasoning, context, agent, and tool resources
  according to task difficulty and observed progress.

---

## 0. GROUNDING — what already exists (read before building)

The ABC is **~80% integration, ~20% new mechanism**. Almost every resource budget and every
progress signal this PRD needs is already a shipped, tested Drydock module. Building ABC as if
from scratch would duplicate them. The controller's job is to *compose* them behind one policy
and add the one axis Drydock lacks: **targeted resource escalation** (reasoning/context/agents/
tools) driven by the plateau signal that today only drives prompt-firmness nudges.

| PRD concept | Existing module | What it already does | Gap ABC fills |
|---|---|---|---|
| Tool budget (§3), Budget Ledger (§16) | `budget.py` `BudgetState` | request/task/session scopes; tool-call & recovery ceilings; `to_dict()` ledger | reasoning + context + agent budgets as peers; escalate/de-escalate the limits |
| Agent budget (§3, §8) | `capacity.py` `swarm_size()` | `min(concurrency, task_demand, budget)` auto-sizing; server concurrency probe | drive `budget_agents` from the plateau signal, not just task-demand |
| Progress detection (§7, §10) | `progress.py` `assess_action`, `ProgressTracker`, `is_stall`, `no_progress_streak`, `recommended_stage` | scores each action; sliding window; stall streak → recovery stage | expose the same streak to resource escalation, not only prompt recovery |
| Escalation ladder (§9) | `progress.py` `STAGE_AFTER = {1:2,2:3,3:4,4:6,5:8}` + `recovery.py` `RecoveryController` | streak → escalating *guidance strings* → honest terminate | map each stage onto a *resource* change (the new axis) |
| Doom loops (§11) | `loop_detect.py` `LoopTracker`, `tool_signature`, `runaway_repetition_len` | exact-repeat + degenerate-arg detection; advisory notes | feed loop events into "what resource is missing?" |
| Targeted escalation "what's missing" (§8) | `bottleneck.py` `Bottlenecks` | rank known components by share×headroom; name the limiting factor | use it to choose WHICH budget to raise |
| Ratchet integration (§14) | `context_ratchet.py`, `ratchet.py` | measures/checkpoints progress; rolls back code+context (Gate 4) | consume its per-round pass/total as the ABC progress signal |
| Modular context (§12, §13) | `context_runtime.py`, `context_window.py`, `context_inject.py` | assemble prompt from modules; page/unload; budget by token share | let ABC decide module count / token budget as a resource |
| Controller learning (§17), calibration (§18) | `predictions.py` `Register` | pre-registered claim + falsifier + calibration | store envelope→outcome as training signal |

**Design rule:** ABC adds exactly one new file for the policy (`adaptive_budget.py`) and wires it
in; it does not reimplement budgets, progress, loops, or capacity.

### The one genuinely new idea
Today the plateau streak drives `RecoveryController`, which only escalates **prompt firmness** and
ultimately terminates. ABC adds a parallel consumer of the *same* streak that escalates a
**resource envelope** — and, crucially, **de-escalates** it (§15) once discovery makes a hard task
easy. "Failure ≠ think harder": the controller picks *which* of reasoning / context / agents / tools
to change, per §8.

### MVP status (§20) — SHIPPED as pure logic
`drydock/adaptive_budget.py` + `tests/test_adaptive_budget.py`. Provider-independent, no live model,
composes `BudgetState`, `ProgressTracker`, `capacity.swarm_size`. It implements: initial reasoning
level from a probe, plateau→one-level reasoning escalation on two consecutive plateaus, then targeted
context/agent escalation, de-escalation on recovery, and a run ledger. **Not yet wired:** the
backend adapter that translates `ReasoningBudget` into llama.cpp/vLLM controls, and the TUI/agent-loop
integration — both touch the live loop and must be validated hands-on through the TUI (per
`feedback_actually_use_drydock_tui`), not headless.

> **Provider constraint:** the reasoning-effort adapter must map to the **local fleet** (llama.cpp
> `--reasoning-budget`, vLLM) and honor `feedback_no_anthropic_api`. For a model without explicit
> reasoning controls, approximate via iteration/generation limits — never by calling a hosted API.

---

## 1. Problem

Drydock currently risks treating very different problems similarly from an inference-resource
perspective. "What command lists the files in this directory?" should not receive the same inference
budget as "Find and repair the concurrency bug, run the suite, and verify no regressions."

LLM systems expose reasoning effort, token limits, context limits, tool calls, and agent count.
These are normally selected before execution and remain relatively static. Two failure modes result:

- **Overthinking:** easy problems consume unnecessary tokens, latency, context, tool calls, compute.
- **Underthinking:** hard problems get insufficient reasoning/exploration → premature answers,
  repeated failures, loops, abandonment.

Drydock should treat inference compute as a resource allocated dynamically: begin cheaply, measure
progress, and increase resources only when evidence suggests more computation is useful.

## 2. Product Thesis

Not "how much compute does this task need?" (predicted perfectly up front) but: **start with the
cheapest plausible budget, observe progress, escalate resources only when justified.** Feedback loop:

```
Task → Handshake Probe → Initial Resource Envelope → Execute → Observe/Verify
   → useful progress? — yes → Continue ; no → Escalate → Ratchet → Final Result
```

This extends Handshake from an agent-sizing mechanism (`capacity.swarm_size`) into a general
inference resource controller.

## 3. Resources Under Control

| Budget | Controls | Existing home |
|---|---|---|
| Thinking | reasoning effort / tokens / iteration depth | new `ReasoningBudget` (this PRD) |
| Context | context size + which modules load | `context_runtime.py` |
| Agent | number / specialization / concurrency | `capacity.swarm_size` |
| Tool | number / frequency / type of tool calls | `budget.BudgetState.max_tool_calls` |

A fifth (time/compute: wall-clock, GPU-seconds, tokens, cost) can be added later. Dimensions are
**independently adjustable** — failure does not necessarily mean "think harder": a missing-information
failure raises context; an uncertain-between-approaches failure raises agents.

## 4. Handshake Probe

Every nontrivial request enters a lightweight Handshake stage under a deliberately restricted budget.
Purpose is **classification, not solution generation.** It estimates:

```
task_type: coding
complexity: medium
ambiguity: low
tool_dependency: high
verification_available: true
estimated_scope: local
parallelizable: false
context_requirements: [repository_map, target_files]
risk_of_long_horizon: low
```

Prefer **measurable characteristics** over model intuition: files implicated, repo size, number of
requested operations, available tests, dependency-graph depth, tool requirements, compiler/test
failures, external-info need, count of independent hypotheses, previous attempts/failures, verifier
confidence.

## 5. Initial Budget Selection

Handshake converts the probe into a Resource Envelope:

```
budget:
  reasoning: {level: low, max_tokens: 4000}
  context:   {modules: 2, max_tokens: 16000}
  agents:    {count: 1}
  tools:     {max_calls: 10}
  execution: {max_iterations: 4}
```

Implementation must be **model-provider independent**. Drydock exposes an abstraction:
`ReasoningBudget(level="low", token_budget=4000, iterations=4)`. The backend adapter translates it:
`reasoning_effort=low` for one model, a thinking-token budget for another, or (no explicit control)
approximation via iteration/generation limits or agent strategy.

## 6. Budget Levels

Four presets — **presets, not hard boundaries**; the controller eventually operates on continuous
resource values.

| Level | Typical workload |
|---|---|
| Minimal | obvious/simple operation |
| Low | straightforward localized task |
| Medium | multi-step reasoning/debugging |
| High | difficult, ambiguous, repo-wide or long-horizon |

## 7. Dynamic Escalation (core feature)

After execution begins, Ratchet + verifiers stream progress signals. `17→19 passed` = useful
progress, keep the budget. `19→19→19→19` = no measurable progress, reconsider allocation. This is the
`ProgressTracker.no_progress_streak` signal already in the loop.

## 8. Escalation Policy (targeted, not uniform)

- **Reasoning ↑** when evidence exists but inference looks wrong / weak approaches repeated / deeper
  planning needed / contradictions unresolved. `reasoning: LOW → MEDIUM`.
- **Context ↑** when the model repeatedly lacks info / relevant symbols missing / dependencies cross
  context boundaries / current module insufficient. `module_auth → module_auth + module_database`.
  Integrates directly with the modular-context architecture.
- **Agent ↑** when several plausible hypotheses exist / search space broad / independent exploration
  useful / current approach plateaued. `1 agent → 3 hypothesis agents → best candidate`.
- **Tool ↑** when uncertainty is resolvable experimentally. *"Don't give the model another 10,000
  reasoning tokens. Run the test."* — potentially a large efficiency win.

Which axis to raise is a `bottleneck.py`-style question: name the limiting factor, attack that one.

## 9. Escalation Ladder

```
L0 cheap probe → L1 small reasoning/context/1 agent → (fail) L2 more reasoning
→ (fail) L3 +context module → (fail) L4 +tools/experiments → (fail) L5 agent fan-out
→ (fail) L6 long-horizon / Ratchet mode
```

Maps onto existing `STAGE_AFTER = {1:2,2:3,3:4,4:6,5:8}`. Ordering should eventually be **learned**,
not fixed — some tasks escalate context before reasoning; some benefit immediately from parallelism.

## 10. Progress Detection

Progress is a first-class object. Coding signals: tests passing, compiler errors, lint failures,
benchmark score, changed failing tests, repo state, new verified info (all already scored by
`progress.assess_action`). Softer signals for test-less tasks: new evidence, hypothesis eliminated,
subtask completed, uncertainty reduced, artifact produced, critic score.

```
ProgressSignal(score=0.71, confidence=0.93, source="pytest", previous=17, current=21)
```

## 11. Detecting Doom Loops

Handshake keeps a lightweight history: actions attempted, files modified, commands executed, errors,
rejected hypotheses, progress scores (`loop_detect.LoopTracker` already does the action side).
Repeated semantically-equivalent actions without fitness improvement trigger a **plateau event** →
NOT automatically "MORE TOKENS" but: *what resource is missing?* → targeted escalation. More
inference isn't necessarily better inference.

## 12. Integration with Modular Context

ABC and MCR share one controller. Handshake keeps a small persistent base (User Request, Task State,
Current Goal, Constraints, Ratchet State, Known Facts, Failed Approaches); everything else is a
module (`repository_map`, `authentication`, `tests`, `database`, `api`, `previous_attempts`).
Handshake decides which modules are active → context becomes an inference-time resource. Not
"context window = 128K" but "this task needs 14K of relevant context."

## 13. Context Pressure Response

Don't immediately compact everything. Choose among: unload irrelevant module → compact one module →
replace module with summary → retrieve another module. When a subtask finishes, `UNLOAD` its module
(28K → 20K) rather than compacting the whole conversation. (MCR §0 measured that compaction
structurally wins the race against paging tool results — ABC's module-unload is the intended lever.)

## 14. Handshake vs Ratchet (distinct responsibilities)

Handshake **allocates** resources; Ratchet **measures & preserves** progress. Closed loop:
`Handshake allocate → Execute → Ratchet measure/checkpoint → progress? yes: continue / no: Handshake
reallocate`.

## 15. Budget Controller

```python
class AdaptiveBudgetController:
    def probe(task): ...
    def allocate(task_state): ...
    def observe(progress): ...
    def escalate(reason): ...
    def deescalate(reason): ...
    def terminate(reason): ...
```

**De-escalation must exist:** a hard task that becomes straightforward after root-cause discovery
should drop back (HIGH/4 agents/40K → LOW/1 agent/12K), so escalation never becomes permanent.

## 16. Budget Ledger

Every run records what was spent (extends `BudgetState.to_dict()`):

```json
{"reasoning_tokens": 18342, "input_tokens": 44121, "tool_calls": 27, "agent_runs": 4,
 "context_modules_loaded": 7, "wall_time_seconds": 412, "ratchet_improvements": 6}
```

Enables learning which allocation strategies work.

## 17. Learning the Controller

V1 heuristics. V2 learns from Drydock traces: `task characteristics + initial allocation +
escalation history + resource consumption + final success/failure`. Eventually: "tasks resembling
this usually succeed with medium reasoning, 1 agent, 2 context modules." Accumulated traces become
training data for Drydock's own inference-resource policy. Use `predictions.Register` for calibration.

## 18. Metrics

Optimize **success per unit compute**, not raw success. Track: task success rate; tokens / success;
GPU-seconds / success; time to first verified improvement; total wall time; tool calls / success;
agent runs / success; context tokens / success; escalations / task; doom-loop frequency.

```
Inference Efficiency = Verified Task Progress / Compute Consumed
```

## 19. Experimental Plan

Same TerminalBench tasks (per `feedback_tbench_only_focus`, tbench-2 only, driven THROUGH the real
TUI per `feedback_drydock_is_the_harness`) under three arms:

- **A — Fixed Low:** low reasoning, 1 agent, fixed context.
- **B — Fixed High:** high reasoning, multiple agents, large context.
- **Experimental — Adaptive:** cheap probe → dynamic reasoning/context/agents/tools.

Compare success, tokens, GPU time, wall time, loop frequency. Hypothesis is **economic**: Adaptive
approaches max-compute performance while consuming substantially less compute over the task
distribution — and may *outperform* fixed-max by changing the *kind* of resource applied, not just
adding tokens. (Honor `feedback_no_custom_eval_harness`: measure through the TUI, not a batch judge.)

## 20. MVP

Deliberately small — enough to test the central hypothesis before a sophisticated scheduler:

1. Handshake assigns an initial reasoning level: LOW / MEDIUM / HIGH.
2. Ratchet reports measurable progress or plateau.
3. Two consecutive plateau events trigger one level of reasoning escalation.
4. Continued plateau triggers context expansion or agent fan-out.

**Shipped** as `drydock/adaptive_budget.py` (see §0). Remaining: provider adapter + TUI wiring.

## 21. Longer-Term Architecture

Drydock as an **inference operating system**: model = processor, context modules = memory, agents =
parallel processes, tools = I/O, Ratchet = feedback/checkpointing, Handshake = the scheduler deciding
where inference compute goes. Principle: *don't give every problem the largest context, max reasoning,
and max swarm — allocate dynamically per demonstrated need.* Reasoning itself becomes a modular,
dynamically-allocated resource rather than a fixed property of an agent run — the natural completion
of the modular-context idea.

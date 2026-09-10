# Drydock Multi-Agent Swarm — Product Requirements Document

- **Product:** Drydock
- **Feature:** Multi-Agent Swarm / Parallel Problem Solving
- **Status:** Proposed → **MVP IMPLEMENTED** (`drydock/swarm.py`, `drydock swarm` CLI) — see "Implementation Status" below
- **Version:** 1.0
- **Date:** September 2026

> **Implementation note (2026-09-09).** This feature is built **MVP-first** (§35),
> grounded in Drydock's existing primitives rather than greenfield: the `Dispatch`
> sub-agent tool (programmatic agent launch), the ratchet/eratchet worktree isolation, the
> `events.py` stream, the `tool_registry`/agent-loop, and the file-based job-queue pattern
> already proven in `research/selfdistill/cluster`. The core question the MVP must answer
> (§35) is: **does coordinated parallel exploration materially increase Drydock's task
> completion rate?** Everything past the MVP is gated on a "yes" measured on the same
> tbench-class tasks Drydock already targets.

> ## Implementation Status (2026-09-09)
>
> **MVP shipped** as `drydock/swarm.py` + the `drydock swarm` subcommand (`cli.py`), 24
> tests in `tests/test_swarm.py`, gate-clean (ruff / pyright / suite). Delivered in four
> slices:
> - **Blackboard** (§9/§10/§28) — `Blackboard`: append-only JSONL under
>   `.drydock/swarms/<id>/` (discoveries/hypotheses/candidates/tasks), unified `EventLog`
>   (§27), `create/open/list/latest_swarm`, resumable.
> - **Worker** (§16/§17/§32) — `run_worker`: each Builder runs the in-process agent loop
>   (`default_agent_runner`, the Dispatch recipe) in its own detached git worktree; result
>   snapshotted as a commit and recorded as a `Candidate`; crashes contained. Runner is
>   injectable for model-free testing.
> - **Coordinator + verify + judge** (§6/§15/§18/§20/§24) — `run_swarm`: Parallel strategy
>   with `diversify()` (8 angles), `ThreadPoolExecutor` fan-out over one shared server
>   (§22), independent scoring via `make_shell_verifier` (reusing `ratchet.score_output` /
>   `detect_verifier`), `judge()` by evidence, `SWARM_CONVERGED` on all-pass.
> - **CLI + status** (§25/§26) — `run_cli`: `solve` / `status` / `list` / `resume`;
>   `render_status()` ranked view + cherry-pick hint.
> - **In-TUI + auto-escalation** (§40, primary UX) — the user just **prompts normally**; they
>   do NOT invoke a swarm or pick an agent count. After a normal single-agent turn, the
>   harness auto-escalates to an **in-process** swarm (no extra TUI windows) via
>   `should_auto_escalate()` — policy = **escalate-on-difficulty**: a known verifier has
>   failed enough times in a row (reusing the ratchet's `verify_fail_streak` /
>   `ratchet_offer` signal ⇒ hard AND verifiable) and the repo supports worktree isolation.
>   Progress streams into the same session (`on_event`); workers are ephemeral (no resume
>   pollution). `/swarm <objective> [--agents N]` remains as a manual override. Compute-safe
>   by construction: swarm cost is spent only when a single agent is demonstrably stuck on a
>   verifiable task (§39 "more agents ≠ more intelligence").
>
> **Verified with an injected runner** (worktree/verify/judge/git plumbing proven end-to-end
> on real temp repos). The `default_agent_runner` path against a **live model** is validated
> separately by a real 2-agent run (see the run log referenced in RESUME).
>
> **Auto-sizing (how MANY agents) — resource detection BUILT.** `drydock/capacity.py`
> detects the inference server's real concurrency (explicit `swarm_concurrency` config /
> env → llama.cpp `/slots` → empirical burst probe → default), and `swarm_size()` sets
> N = min(hardware concurrency, task-demand, budget), never maximized (§39). `agents="auto"`
> (the TUI default + auto-escalation) uses it: an 8-GPU vLLM box gets hammered (task-demand
> binds), a `-np 2` laptop is toned down (hardware binds). **Still deferred:** the fully
> *adaptive* sizer that grows N mid-run only while agents keep producing distinct verified
> candidates (the `candidate_diversity`/novelty signal) — capacity detection sets the
> ceiling; adaptive growth toward it within a task is the next refinement.
>
> **Deliberately deferred to Phase 2/3** (§36/§37): dynamic spawning, specialized
> Explorer/Critic/Tester/Reviewer roles, hypothesis tracking as a first-class loop,
> tournament/debate/map-reduce strategies, heterogeneous per-role models, context
> compression, advanced anti-loop novelty scoring, distributed/hierarchical execution.

---

## 1. Executive Summary

Drydock currently provides an agentic environment in which an LLM can inspect a problem,
execute tools, modify files, run commands, test solutions, and iterate toward completion.

This PRD introduces a **Multi-Agent Swarm** capability that allows Drydock to deploy a
configurable number of autonomous agents against the same high-level objective. Instead of
relying on one agent to discover the entire solution sequentially, Drydock coordinates many
agents that can investigate different hypotheses; explore different portions of a codebase;
attempt independent solutions; specialize in different roles; critique other agents' work;
reproduce failures; run experiments in parallel; share discoveries; propose candidate
solutions; verify candidate solutions; integrate successful work; and collectively converge
on a final answer.

The system supports anything from 2 agents to potentially hundreds, constrained primarily
by available compute, inference capacity, API limits, context management, and user-defined
budgets.

**Core design principle: parallelize uncertainty, not merely execution.** Agents should not
duplicate work. Drydock intentionally creates diversity in hypotheses, approaches, roles,
and experiments while continuously consolidating useful information.

## 2. Problem

Complex software and reasoning problems frequently require exploration. A single agent
operates largely sequentially (Observe → Reason → Act → …). It may commit too early to an
incorrect hypothesis; become trapped in loops; repeatedly attempt similar fixes; overlook
portions of a repository; fail to challenge its own assumptions; consume large context
windows; spend time on dead ends; forget earlier discoveries; or produce a plausible but
under-verified solution.

Drydock already provides the tools for an agent to interact with a computing environment.
The next step is to **parallelize the reasoning process itself** — from `Problem → Agent →
Solution` to a coordinated `Problem → Coordinator → {Agents / approaches} → Shared Knowledge
→ {Builder/Critic/Tester} → Judge → Verified Solution`.

## 3. Goals

- Assign a problem to multiple autonomous agents.
- **Coordinate** agents rather than launching independent identical sessions.
- Support parallel exploration of multiple solution strategies.
- Let agents share high-value discoveries **without** sharing complete contexts.
- Detect duplicated work and unproductive agent loops.
- Let successful discoveries redirect other agents.
- **Independently verify** proposed solutions before declaring completion.
- Configurable limits for compute, tokens, time, agents, inference calls.
- Work with both local/open-weight models and remote/API models.
- SHOULD allow heterogeneous models in the same swarm.

## 4. Non-Goals

- V1 is not a fully decentralized artificial society.
- Agents need not communicate freely with every other agent.
- More agents ≠ automatically better performance.
- Agents need not hold the entire repo or full swarm history in context.
- Avoid unrestricted agent-to-agent chatter (burns tokens without producing work).

## 5. Core Concept — Six Components

`OBJECTIVE → COORDINATOR → WORKER AGENTS → KNOWLEDGE/MEMORY (blackboard) → CRITICS/VERIFIERS
→ FINAL SOLUTION`. The six primary components are: **(1) Coordinator, (2) Task Graph,
(3) Worker Agents, (4) Shared Knowledge Store, (5) Critics/Verifiers, (6) Final
Integrator/Judge.**

## 6. Coordinator

Swarm-level reasoning; does NOT do most implementation itself. Responsibilities: understand
the objective; generate an initial decomposition; identify uncertainty; generate competing
hypotheses; create/assign tasks; monitor progress; identify duplicated effort; kill
unproductive tasks; spawn additional investigation; promote important discoveries; initiate
verification; decide when there is enough evidence to attempt integration. Maintains a
**dynamic Task Graph** that evolves as agents discover new information.

## 7. Agent Roles

Roles are **behavioral prompts + permission profiles**, not necessarily different models:

- **Explorer** — investigate without modifying the canonical workspace (repo exploration,
  dependency analysis, docs, call-graph tracing, hypothesis generation).
- **Researcher** — investigate one narrowly defined question.
- **Builder** — create a candidate implementation; normally in an isolated worktree/sandbox.
- **Debugger** — reproduce failures, identify root causes.
- **Critic** — adversarially find reasons a hypothesis/implementation is wrong.
- **Tester** — construct/run tests designed to falsify a candidate.
- **Reviewer** — examine diffs for correctness, maintainability, regressions, complexity,
  security.
- **Integrator** — combine successful work from multiple agents.
- **Judge** — evaluate evidence and decide whether the objective is actually satisfied.

## 8. Agent Independence

Independent reasoning is critical — if ten agents immediately receive Agent 1's reasoning
they converge prematurely. Distinguish **Private Context** (per-agent conversation history,
scratch reasoning, tool history, local observations, experimental state) from **Shared
Knowledge** (only high-value findings are promoted). This preserves diversity while allowing
collaboration.

## 9. Shared Knowledge Store

Structured store, e.g.:

```
.swarm/
    objective.json
    task_graph.json
    discoveries.jsonl
    hypotheses.jsonl
    failures.jsonl
    decisions.jsonl
    candidates/
    artifacts/
    metrics.json
```

A discovery record:

```json
{
  "agent": "agent-17",
  "task": "task-42",
  "type": "discovery",
  "confidence": 0.91,
  "summary": "Token refresh path bypasses expiration update.",
  "evidence": ["src/auth/refresh.py:118", "tests/test_refresh.py::test_expiration"]
}
```

Agents consume **summaries** of relevant discoveries, not full transcripts.

## 10. Blackboard Architecture

Preferred communication model: a **blackboard**. Agents publish findings to a common
knowledge layer; others subscribe to what is relevant to their task. The blackboard holds
confirmed facts, suspected facts, hypotheses, rejected hypotheses, test results, failure
signatures, candidate patches, and unresolved questions. This avoids the O(N²) all-to-all
communication problem.

## 11. Hypothesis Management

Track hypotheses explicitly. Each has supporting evidence, contradicting evidence,
investigating agents, a confidence score, and a status:
`UNTESTED → INVESTIGATING → SUPPORTED → WEAKENED → REJECTED → CONFIRMED`. The Coordinator
allocates more resources to promising hypotheses while keeping some exploration budget for
alternatives.

## 12. Dynamic Agent Spawning

Agent count is not fixed. e.g. `drydock swarm --agents 32 --max-agents 100 --objective "…"`.
The Coordinator may deploy a mix (explorers/debuggers/investigators/builders/critics), then
later terminate some and create testers/reviewers/integrators. **Agents are temporary
compute resources, not permanent identities.**

## 13. Agent Lifecycle

`CREATED → ASSIGNED → RUNNING → {DISCOVERY | BLOCKED | FAILED | REDUNDANT | COMPLETED} →
TERMINATED`. The Coordinator can terminate an agent at any time.

## 14. Anti-Loop System

Swarm operation includes explicit loop detection via an **Agent Novelty Score**. Signals:
repeated command; repeated tool call; repeated file access; semantically similar reasoning;
repeated error; unchanged workspace; repeated patch; lack of new evidence. Low novelty +
zero new discoveries + zero workspace changes ⇒ **terminate** and reassign the task to a
different strategy. (Directly addresses the long-loop behavior already observed in Drydock.)

## 15. Diversity Injection

When spawning against uncertain problems, deliberately generate different approaches
(simplest explanation; assume the obvious is wrong; dependency/version problems; trace
backward from the failing test; inspect recent changes; minimal reproduction; concurrency;
fully independent solution). **The goal is search-space coverage.**

## 16. Workspace Isolation

Agents modifying code MUST NOT share a working tree concurrently. Each Builder gets an
isolated environment — preferably a **git worktree** (`worktree-agent-01`, …). Each candidate
patch stays independently testable; successful patches are cherry-picked/merged by an
Integrator.

## 17. Candidate Solutions

Candidate implementations are **first-class swarm objects** (author, hypothesis, files
changed, tests passed, new tests, reviewer score, critic findings, status). Candidates
compete on **evidence**, not agent confidence.

## 18. Verification Pipeline

No Builder declares its own solution complete. Candidates pass independent verification:
`Builder → Candidate → Tester → Critic → Reviewer → Judge`. The system actively attempts to
**falsify** candidates; a solution is accepted only when sufficient independent evidence
exists.

## 19. Swarm Convergence

Explicit stopping condition, e.g.: `required tests pass AND no critical critic findings AND
minimum independent verification count reached AND judge confidence ≥ threshold`.

## 20. Confidence From Evidence

Agent self-reported confidence has limited weight. Confidence derives from observable
evidence (+ tests passed, reproduction eliminated, independent confirmation, static analysis,
benchmark improvement, multiple approaches agree; − failing tests, conflicting observations,
critic objections, unexplained behavior, incomplete coverage).

## 21. Resource Manager

Users specify `--agents / --max-agents / --max-runtime / --token-budget / --gpu-budget /
--api-budget`. The Resource Manager tracks active/queued agents, tokens consumed, GPU
utilization, API expenditure, elapsed time, completed/failed tasks.

## 22. Local Model Scheduling

Multiple agents share **one** inference server. Agents are logical processes — no separate
model copy in VRAM. A 32-agent swarm = 1 model, 1 inference server, 32 independent contexts,
32 agent state machines; requests are continuously batched by the backend. **This is the key
efficiency: agents are contexts, not model replicas.**

## 23. Heterogeneous Model Swarms

Different models per role (coordinator: large reasoning; explorers: fast 7B; builders: strong
coding; critics: separate reasoning; judge: highest quality). Config maps role → model. Can
dramatically reduce total inference cost.

## 24. Swarm Strategies

- **Parallel** — all agents independently attempt the whole problem (small but uncertain
  solution space).
- **Decompose** — Coordinator divides into subtasks (large repos).
- **Tournament** — many candidates progressively eliminated (32 → 16 → 8 → 4 → 2 → winner).
- **Debate** — competing explanations, critics challenge them.
- **Map-Reduce** — agents analyze portions, Integrator combines.
- **Adaptive** — Coordinator chooses strategy from current state. **Adaptive should
  eventually be the default.**

## 25. Swarm CLI

```
drydock swarm --objective "Fix all failing tests" --agents 20
drydock swarm --objective "Solve this TerminalBench task" --agents 32 \
    --strategy adaptive --max-agents 64 --runtime 45m --verify 3
drydock swarm resume swarm-20260909-001
drydock swarm status
```

## 26. User Interface

The terminal exposes swarm state in real time: objective; active/spawned agents; task
progress; hypotheses with confidence; candidates; runtime; leading hypotheses; per-agent
role + current activity; best candidate with test counts / critic pass / independent
verification / status.

## 27. Event System

Event-driven: `TASK_CREATED, TASK_ASSIGNED, AGENT_STARTED, AGENT_BLOCKED, DISCOVERY_CREATED,
HYPOTHESIS_CREATED, HYPOTHESIS_REJECTED, CANDIDATE_CREATED, TEST_COMPLETED, CRITIQUE_CREATED,
AGENT_LOOP_DETECTED, AGENT_TERMINATED, SWARM_CONVERGED`. Lets future UIs/visualizers observe
without coupling to orchestration logic.

## 28. Persistent Swarm State

A swarm survives interruption:

```
.drydock/swarms/swarm-001/
    objective.json  config.yaml  agents.json  tasks.json  task_graph.json
    knowledge.jsonl  events.jsonl  candidates/  worktrees/  metrics.json
```

Resumable after terminal closure, inference-server restart, reboot, API failure, agent crash.

## 29. Scaling Architecture

- **Level 1 — Single machine** (Drydock → inference server → 2–32 agents). **This is the MVP.**
- **Level 2 — Multiple GPUs/servers** (Coordinator → task queue → nodes).
- **Level 3 — Distributed** (Coordinator → message broker → worker nodes; Redis/NATS/ZeroMQ).
  Distributed infra must NOT be required for normal operation.

## 30. Hierarchical Swarms

At large counts one Coordinator bottlenecks. Support `Swarm Commander → Team Leads → workers`
(e.g. 1 commander + 5 team coordinators + 94 workers). Team coordinators summarize upward.

## 31. Context Compression

Agents do NOT receive the entire swarm history. The context builder assembles only:
objective + current task + relevant repo context + relevant shared discoveries + relevant
rejected hypotheses + agent role + tool state. Keeps context bounded across thousands of
events.

## 32. Failure Handling

Tolerate individual agent failure (crash, model timeout, malformed response, tool error,
context overflow, loop, invalid patch, inference disconnect). No single failure terminates
the swarm; the task returns to the queue or is reassigned.

## 33. Metrics

`time-to-solution, tokens-to-solution, agent-hours, #hypotheses, hypothesis survival rate,
candidate count, test success rate, agent loop rate, duplicate-work rate, verification count,
solution success rate`. Research metric: **Marginal Agent Utility** — improvement at
1/2/4/8/16/32/64 agents, to find where added parallelism stops helping.

## 34. Benchmarking

Evaluate on the classes Drydock already targets (TerminalBench, SWE-bench, repo repair,
debugging, dependency resolution, security analysis, long-horizon terminal). Compare single
vs 4/8/16/32-agent swarms on both **success rate and total inference cost**.

> **Repo constraint:** model-accuracy evaluation stays **TUI-driven** and must not become a
> headless judge/eval harness (project rule). Swarm benchmarking measures the *feature*
> (does coordination lift completion rate at what cost), driven through Drydock itself.

## 35. MVP

Supports: Coordinator; shared task queue; **2–8 workers**; isolated git worktrees; shared
discovery store; candidate solutions; independent verifier; token/time budget; basic loop
detection; terminal status display. Does NOT require: distributed compute; hierarchical
coordinators; hundreds of agents; reputation systems; vector DBs; complex consensus. **The
MVP exists to answer: does coordinated parallel exploration materially increase Drydock's
task completion rate?**

## 36. Phase 2

Dynamic spawning; specialized roles; hypothesis tracking; critic agents; tournament
selection; adaptive task allocation; heterogeneous models; context compression; advanced
anti-loop logic. Target: **8–32 agents**.

## 37. Phase 3

Distributed execution. Target **32–256+ agents**: remote worker nodes; multiple inference
servers; GPU-aware routing; hierarchical coordination; fault-tolerant queues; distributed
workspaces; swarm visualization.

## 38. Example Execution

`drydock swarm --agents 32 --strategy adaptive "Fix the bug causing the application to
crash."` → Coordinator decomposes → explorers/reproducers/analyzers spawn → Agent 7
discovers "crash occurs only after cache refresh" → blackboard → Coordinator spawns
"investigate cache refresh path" → agents converge on a suspicious function → three Builders
create competing patches → Testers score them → Critics attack the leader, find an edge case
→ Builder produces B2 → all test tiers pass → two independent agents reproduce-and-confirm
the fix → Judge: confidence 0.97, **SWARM CONVERGED** → integrate B2.

## 39. Critical Design Principle

Avoid assuming **more agents = more intelligence** (100 identical agents = 100 copies of the
same mistake at 100× cost). The real equation:
`MORE AGENTS + DIVERSITY + SPECIALIZATION + SHARED DISCOVERY + ADVERSARIAL REVIEW +
VERIFICATION + RESOURCE ALLOCATION = SWARM ADVANTAGE`. The Coordinator's primary job is the
**allocation of cognition** — where added reasoning has the greatest expected value.

## 40. Long-Term Vision

Drydock evolves from an agent harness into a **compute-to-reasoning orchestration system**.
`drydock solve problem.md --compute 8gpu --time 2h --agents auto` → Drydock decides how many
agents, which models, which roles, which hypotheses/experiments, when to abandon dead ends,
when to scale parallelism up/down, how much verification is warranted, and when enough
evidence exists to declare success. The user stops managing agents and starts **allocating
compute toward a problem**: give Drydock a problem, resources, and success criteria; Drydock
organizes the intelligence to solve it.

---

## Implementation Plan (Drydock-grounded, MVP-first)

The MVP is a **single-machine** swarm (§29 Level 1, §35) reusing Drydock primitives:

- **Worker = the existing agent loop** (the same machinery `Dispatch` uses to run a
  sub-agent to completion), launched programmatically per task with its own cwd + tool
  allowlist + private context.
- **Isolation = git worktrees** — one worktree per Builder (§16), so parallel patches never
  collide (the ratchet/eratchet already runs isolated per-attempt environments; reuse that
  discipline).
- **Blackboard = a `.drydock/swarms/<id>/` directory** of append-only JSONL (discoveries,
  hypotheses, candidates, events) — the same file-queue/claim pattern proven in
  `research/selfdistill/cluster`, but in-repo and stdlib-only.
- **Events = `drydock/events.py`** stream (§27).
- **CLI = a new `drydock swarm` subcommand** following the existing subcommand pattern
  (graphrag/rmf/eratchet).

**First vertical slice (thinnest end-to-end that answers §35):**
1. `drydock swarm --objective "<obj>" --agents N --strategy parallel` on a git repo.
2. Coordinator writes the objective + spawns N Builder workers, each in its own worktree,
   each told (via diversity injection, §15) to take a *different* angle.
3. Each worker runs the real agent loop to a candidate patch; results recorded as
   first-class **candidates** (§17) on the blackboard.
4. An independent **verifier** (§18) runs the repo's tests against each candidate worktree
   (no self-reported success) and a **judge** picks the candidate with the strongest
   evidence (§20).
5. Terminal **status display** (§26) + persistent, resumable state (§28) + a token/time
   **budget** (§21) + basic **loop detection** (§14).

Ship in small verified steps (blackboard store → worker launch+worktree → verifier/judge →
CLI+status), each with real functional tests through Drydock, before adding Phase-2 roles,
hypotheses, tournaments, and heterogeneous models.

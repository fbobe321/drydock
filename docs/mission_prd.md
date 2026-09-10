# Drydock Long-Horizon Autonomous Missions — PRD (condensed design-of-record)

- **Feature:** Long-Horizon Autonomous Mission Execution
- **Status:** Proposed → **MVP in progress** (Phase 1)
- **Version:** 1.0 · **Date:** 2026-09-10

> **The principle (§50/§52):** *missions* run for hours/days; *agents* are disposable
> workers doing bounded units against durable state. **"Do not make the agent's loop longer;
> make the work durable."** Drydock stops being a CLI-around-an-LLM and becomes an operating
> system for autonomous work. **Kept deliberately separate from the swarm** — build Missions
> first with a single worker; then the durable queue, leases, task deps, evaluator, and
> persistent state become exactly the control plane a swarm needs.

## The abstraction
A **Mission** is a durable, goal-oriented process. It decomposes an objective into
**Milestones → Tasks → Actions**, plus **Experiments** (hypothesis → change → measure →
KEEP/REVERT). Roles: **Mission Manager** (deterministic controller), **Planner** (objective →
tasks), **Worker** (bounded task, disposable), **Evaluator** (deterministic measurement,
KEEP/REVERT), **Critic** (invoked on stagnation). All state lives outside model context.

## Grounding in existing Drydock primitives
- Durable state → **SQLite** (`events.py` already ships `SQLiteEventLog`).
- Event log (§12) → the missions `events` table (immutable, append-only).
- Worker (§7.3) → the existing `agent.run` loop (as the swarm's `default_agent_runner` uses).
- Evaluator (§7.4) → `ratchet.score_output` / `detect_verifier` (deterministic > model judgment).
- Checkpoints (§18) → git snapshots (the eratchet/swarm pattern).
- Crash recovery (§28) → `resume.py` idioms + lease reclaim.
- CLI (§36) → the `eratchet`/`swarm` argv-intercept pattern in `cli.py`.

## MVP (§49) — what Phase 1 must prove
> Drydock can autonomously work a repository for **≥6 hours**, **survive interruption**,
> **avoid repeating known failures**, and show **measurable progress within budget** — with a
> single worker, before any multi-agent execution.

MVP set: Mission + SQLite + Task Queue + Planner + Worker + Evaluator + Git Checkpoint +
Experiment History + Progress/Stagnation Detection + Resume.

## Acceptance tests (§47)
AT-1 six-hour mission (no continuous LLM convo) · AT-2 Drydock restart → `resume` keeps work
· AT-3 worker crash → lease expires → another resumes · AT-4 context exceeds window → runs on
reconstructed context · AT-5 regression → evaluator reverts + records negative knowledge ·
AT-6 literal loop interrupted · AT-7 semantic stagnation → escalation · AT-8 repeated failed
strategy → surfaces negative knowledge + demands new justification · AT-9 budget exhaustion →
stops · AT-10 success → stops + final report.

## Phases (§48)
1. **Durable Missions** — mission/task models, SQLite state, CLI, event log, crash recovery,
   git checkpoints, single worker. *Goal: run reliably for hours.*
2. **Autonomous Improvement** — planner, evaluator, experiments, baseline, KEEP/REVERT,
   knowledge store, negative knowledge, strategic reviews.
3. **Long-Horizon Reliability** — leases, heartbeats, stagnation + semantic-loop detection,
   escalation ladder, model routing, budgets, context reconstruction.
4. **Swarm Execution** — concurrent workers, dependency scheduler, resource locking, conflict
   detection, parallel evaluators. (Reuses the swarm feature's blackboard/diversity work.)
5. **Mission Intelligence** — expected-value task selection, cross-mission knowledge, etc.

## Definition of done (§51)
Create a persistent mission → decompose → execute bounded tasks → reconstruct context →
measure progress → checkpoint → revert regressions → persist wins → remember failures →
detect loops/stagnation → recover from worker + Drydock restart → enforce budgets → periodic
replanning → stop on success/termination → auditable final report → run ≥6h unattended →
model-independent → provides the task/lease infra for future swarm.

---

## Implementation status (2026-09-10)

**Slice 1 — durable state layer: SHIPPED** (`drydock/mission.py`, `tests/test_mission.py`,
gate-clean). `MissionStore` (SQLite, WAL, transactional, model-neutral):
- **missions** (objective, success_criteria, status §8, budget, baseline §9 immutable, current/best metric §38);
- **tasks** queue (§25) with deps → PENDING→READY promotion, priority ordering;
- **atomic leasing** (§26) — single-winner `claim_task`, `heartbeat`, `reclaim_expired` (crash recovery, AT-3);
- **immutable event log** (§12); **budgets/usage** (§29/§30) with `budget_exhausted`;
- workspace factory `.drydock/missions/<id>/state.db` (§40); **persists across reopen** (AT-2).

**Next slices (Phase 1):** (2) mission CLI (`drydock mission create/list/status/resume/…`,
§36) + generated `MISSION.md`/`CURRENT_STATE.md` views; (3) the controller loop (§41) — git
checkpoint → single Worker (`agent.run`) → deterministic Evaluator → KEEP/REVERT → progress
+ stagnation → budget stop; (4) a minimal Planner (objective → tasks) + baseline + experiment
history; (5) `mission resume`. Then the AT-1..AT-10 acceptance run.

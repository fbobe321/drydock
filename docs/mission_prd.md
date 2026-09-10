# Drydock Long-Horizon Autonomous Missions — PRD (condensed design-of-record)

- **Feature:** Long-Horizon Autonomous Mission Execution
- **Status:** Proposed → **Phase 1 + Phase 2 shipped**; Phase 3 in progress
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

**Slices 2–5 — Phase 1 complete: SHIPPED.** Controller loop (§41) — git checkpoint → single
Worker (`agent.run`) → deterministic Evaluator → KEEP/REVERT → progress/stagnation → budget
stop; mission CLI (§36, `create/list/status/tasks/logs/experiments/knowledge/run/resume/
pause/stop`) + generated `MISSION.md`/`CURRENT_STATE.md`; minimal Planner (objective → tasks);
immutable baseline (§9); `mission resume` (AT-2).

**Phase 2 — Autonomous Improvement: SHIPPED.** Knowledge store (§15) + negative knowledge
(§16, retrieval keyed on the *approach* so AT-8 fires; injected into the worker prompt via
§13 context reconstruction); strategic reviews (§24, deterministic from durable state, with an
optional model-backed reviewer hook for §32).

**Evaluator integrity (§7.4) — HARDENED.** A mission may declare `protected_paths`
(`--protect GLOB`) naming the measurement apparatus (verifier scripts, test files). Each
experiment's git diff-since-checkpoint is checked; if a worker touched a protected path the
result is **rejected without running the (possibly rigged) verifier**, reverted, and recorded
as a `tamper` event + negative knowledge — closing the false-pass hole (cf. the eratchet
false-pass bug). The metric is never advanced under a tampered apparatus.

**Metric-noise policy (§9/§20) — DONE.** The verifier evaluator runs the check `--samples N`
times and takes the MEDIAN (one flaky run can't flip KEEP/REVERT), and KEEP requires the
median to beat the baseline by more than `--noise-band F` points — so a variance-driven blip
is never locked in as a win. Baseline and experiments use the same sampling. Defaults
(1 sample, 0 band) preserve prior behavior; noisy benchmarks should set both.

**Next (Phase 3):** escalation ladder (§23 / AT-7), *semantic* stagnation/loop detection
(§22, beyond the current lexical baseline), fuller model routing (§32). Then the AT-1..AT-10
acceptance run.

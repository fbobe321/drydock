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

**Escalation ladder (§23 / AT-7) — DONE.** Stagnation no longer sends the whole mission
straight to BLOCKED. On stall it climbs a ladder (advisory, never raises §50): L2 Critic
analyses the stall (optional model hook, else a recorded assumption), L3 Planner proposes an
alternative strategy, L5 model routing swaps to a configured `escalation_model`. Because
stagnation must never be "solved" by unlimited iterations (§23), after `max_escalations`
climbs — or when no alternative work can be produced — it stops: `AWAITING_HUMAN` for a
`mission_critical` mission, else `BLOCKED`. Escalations are shown in `mission status`.

**Bounded-worker enforcement (§42) — DONE.** `default_worker` stops at a per-task wall-clock cap
(`--worker-budget S`) and short-circuits the moment the verifier already passes — a worker can't
burn a contended model spinning after the work is done. `--stagnation-limit` / `--max-escalations`
are also exposed for long runs. Per-mission model carry (`--model`/`--base-url`/`--provider`, §33).

**Horizon extenders (small-model productivity) — DONE.** (1) **Decomposing Planner** (§7.2):
inspect the verifier and create ONE focused task per currently-failing check ("make check X pass")
instead of a vague whole-project task — model-sized targets keep a small model productive far
longer; falls back to a generic task when failures can't be parsed. (2) **Discriminating negative
knowledge** (§16/AT-8): key KEEP/REVERT knowledge on the specific target + files changed + what was
tried, not the shared boilerplate objective, so similarity retrieval actually distinguishes prior
failures.

### Validated end-to-end on a real local model (gemma4), 2026-09-10
- **AT-10 success:** `/data3/mission_testbed` (broken pytest suite, `tests/` protected) — gemma4
  took it 40%→**100%**, KEEP + git checkpoint, auto-stopped on `>=100`. Full loop works on a real model.
- **Endurance run** `/data3/mission_endurance` (harder toolkit, easy→hard tail) surfaced the
  small-model **productivity ceiling**: with the trivial planner it climbed 15%→~30% then reverts
  dominated (2 KEEP / 5 REVERT) — motivating the horizon extenders above.
- **Two real bugs fixed while dogfooding:** `ratchet.score_output` miscounted verbose failures
  (first-match grabbed a traceback number → 37.5% vs true 15%); the mission's own `.drydock/state.db`
  was being swept into git checkpoints (a REVERT could roll it back). Both fixed + tested.

**Verdict on days-long autonomy:** *durable execution* for days is solved (state/checkpoints/
resume/bounded workers — no context exhaustion). *Productive* execution for days is gated by the
WORK, not wall-clock: a small model stays productive on broad, decomposable, densely-verifiable
backlogs (many model-sized tasks) and plateaus fast on narrow/hard problems — at which point the
escalation ladder correctly STOPS it rather than spinning. Longer productive horizon ⇒ better
decomposition (the new planner) + a rising model ceiling (self-distill write-back).

**Next (Phase 3):** *semantic* stagnation/loop detection (§22, beyond the current lexical
baseline); fuller model routing across roles (§32). Known wart: an empty queue with no planner
leaves the mission `EXECUTING` (the CLI always supplies a planner, so it only bites programmatic
callers). Then the full AT-1..AT-10 acceptance run.

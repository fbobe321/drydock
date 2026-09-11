# Resource-Aware Adaptive Swarm (ASR) — PRD + design-of-record

- **Feature:** Adaptive Swarm Runtime — turn the swarm from parallel execution into an
  inference-time *search* system.
- **Status:** Reviewed → planned → **Phase 1 + 3 shipped**; Phase 2/4 pending, gated on the pilot.
- **Date:** 2026-09-11

> **Principle (§1):** *Hardware determines concurrency, not swarm intelligence.* A 1-GPU laptop
> can run a 100-agent strategy (mostly sequentially); an 8-GPU box runs the same strategy faster.
> The user gives a problem + a **budget**; Drydock decides how many agents, which deserve more
> compute, how many run at once, and when more search stops being worth it. **Drydock becomes the
> solver; LLM calls are compute units inside a search algorithm** (§27).

## Core distinction (§5)
**Logical agents** (reasoning branches) ≠ **active agents** (currently running inference) ≠
**resident model workers** (GPU-resident backends). 100 logical agents ≠ 100 model copies.

## Review verdict (2026-09-11)
Framing is right and worth pursuing, but **~60–70% already exists** in Drydock — the deliverable
is a thin adaptive layer, not a new stack:
- **Already built:** hardware discovery / calibration / safe concurrency / logical-vs-active split
  → `capacity.py` (`probe_concurrency`, `detect_concurrency`, `slots_concurrency`, `swarm_size`).
  Explore/diversity/evaluate/SwarmMemory → `swarm.py` (`Blackboard`, `Hypothesis`/`Candidate`/
  `Discovery`, git-worktree branches, verify+judge). Evolutionary search + `--share` crossover
  (§13/§14) → `eratchet` + `ratchet_evolve.sh`. Budget/anti-loop/termination substrate → Missions.
- **Genuinely novel (build these):** adaptive compute allocation (§17), marginal-value termination
  (§23), live mid-run concurrency + OOM recovery (§8/§12), budget-first CLI (§15/§16).
- **Biggest risk:** a *fourth* multi-agent stack (swarm.py + eratchet + Missions + ASR). ASR must
  ride the Missions durable state + `capacity.py` + `swarm.py` blackboard — not re-implement them.
- **Honest caution:** self-distill history says gains were **ceiling/data-constrained, not
  search-constrained** — "more search" hits diminishing returns fast on a small model. Build the
  convergence governor (§23) *first* precisely to measure whether the rest pays off.

## Plan (build on the existing substrate)
| Phase | Where | What | Status |
|---|---|---|---|
| 1 Search core | `drydock/asr.py` | `Budget` (§15), `Convergence` (§23), `allocate()` (§17) | **SHIPPED** |
| 3 Planner | `drydock/asr.py` | `plan()` budget+intensity+hardware → population, logical≠concurrency (§5/§16) | **SHIPPED** |
| 2 Live resources | extend `capacity.py` | `SwarmResourceManager`: recompute safe_concurrency mid-run + OOM backoff (§8/§12) | pending |
| 4 Wire-in | `swarm.py` loop | drive generations with allocator+convergence+resource-manager; reuse blackboard/worktrees/judge | pending |
| Gate | — | tbench mission pilot = the §27 go/no-go (adaptive search vs single agent at equal compute) | in flight |

## Implementation status (`drydock/asr.py`, `tests/test_asr.py`, 16 tests, pyright-clean)
- **`Budget`** — parse `2h`/`30m`/`100M-tokens`; `is_finite` guards "run forever" (§15/§10).
- **`Convergence`** — `should_continue()` stops on success / wall / tokens / generations /
  no-improvement-N / **marginal value Δquality/Δcompute below floor** (§23).
- **`allocate()`** — split the next compute pool by EV (`score**bias`), prune weak branches;
  no equal spend on good and bad ideas (§12/§17).
- **`plan()` / `choose_intensity()`** — `light|standard|deep|extreme|auto`; **logical population
  is a search choice, `max_concurrency` a hardware bound** — a deep plan on a 1-slot box keeps all
  16 agents, the excess queues (§5/§18), unlike `capacity.swarm_size` which caps logical at C.

## MVP / gate (§27)
The question — *does adaptive multi-agent search beat a single long-running agent at EQUAL
compute?* — is being answered now by the **tbench mission pilot** (10 unsolved tasks, real TUI on
the dedicated .129 server, task checker as verifier). Same metrics as §27 (Terminal-Bench, tests
passed, tokens, GPU-seconds, wall time). Get that result before building the full apparatus.

## Constraints
- **Objective evidence over LLM judges** (§11) — matches the anti-false-pass work; the swarm judge
  must be tamper-protected like the mission verifier (`--protect`).
- **TUI-only on tbench** — ASR workers on tbench drive the real TUI (the mission-pilot pattern),
  never headless `-p`.

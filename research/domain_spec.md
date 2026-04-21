# Domain Spec: drydock stress harness

Produced per stanford-iris-lab/meta-harness ONBOARDING.md template.
First-cut spec; refine as the proposer exposes failure modes the
current evaluation set doesn't distinguish.

## Domain Summary

**What the user is trying to improve:** drydock's acceptance rate,
throughput, and user-visible reliability on stress workloads. Stable
measurable symptoms from today's feedback session include todo-loops,
text-only turns that don't close, unrendered markdown blobs, and
skip-clusters on heavy prompts.

**Unit of evaluation:** one stress run of 50 prompts against a
pre-bootstrapped `tool_agent` package, hard-capped at 20 minutes. The
kernel-5min variant (25 prompts, 5 min cap) is the held-out smoke
test — fast signal, higher variance.

**Fixed components:**
- Base model: Gemma 4 26B-A4B-it-AWQ-4bit (served by vLLM on
  `localhost:8000/v1`, `tool-call-parser gemma4`,
  `enable-auto-tool-choice`, temperature 0.2, thinking high).
- drydock core: `drydock/core/agent_loop.py` (stabilized 2026-04-19
  after dead-code `should_break_loop` fix), tool implementations,
  provider layer, `_check_tool_call_repetition` flow.
- Stress infrastructure: `research/kernel.py`, `research/mini_prd.md`,
  `research/mini_prompts.txt`, `scripts/stress_shakedown.py`'s
  core loop (pexpect spawn / send / confirm / idle-wait).

**Allowed to change (mutation surface):**
- `drydock/core/prompts/gemma4.md` (Gemma-tuned system prompt —
  currently the single highest-leverage mutable target).
- `drydock/core/prompts/cli.md` (alternate prompt).
- `research/config_best.toml` admiral knobs (7 bounded numerics:
  per_prompt_budget_sec, hard_stop_tool_calls, wrap_up_warn_at,
  stop_now_warn_at, temperature, loop_detector_window,
  struggle_threshold).
- `scripts/stress_shakedown.py` threshold constants
  (MAX_CONSECUTIVE_SKIPS_BEFORE_RESET, SESSION_RESET_EVERY,
  retry windows). NOT the main pexpect loop.
- `drydock/admiral/detectors.py` thresholds + patterns (Check 0
  empty-result match list, consecutive-empty-read escalation).

**Budget:**
- Per candidate: ~20 min (mini-stress 50 prompts) for search-set
  evaluation; 5 min kernel for held-out smoke.
- Per 24 hr: up to ~30 candidates. Overnight ceiling prevents
  vLLM starvation when v10-class long stress runs are active.
- Full 1658-prompt stress used only for quarterly regression. Not
  a per-candidate metric.

## Harness and Search Plan

**Candidate harness interface:** A candidate is expressed as a
`research/candidates/<exp_id>/` directory holding:
- a `config.toml` (admiral knobs in the format
  `research/config_base.toml` already defines),
- optional `gemma4.md` / `cli.md` override files,
- optional `stress_shakedown.patch` (unified diff against the
  frozen main branch's `scripts/stress_shakedown.py` — applied
  only to the kernel's isolated copy, never to source).

The kernel spawns drydock with `HOME=<tmpdir>/home/` so admiral
tuning + config isolation Just Works. Prompt overrides mount over
`$HOME/.drydock/prompts/`.

Interface compliance test: a candidate passes the interface check
iff `kernel.py --config <candidate>/config.toml --validate-only`
loads clean.

**Useful helpers that should exist from the start:**
- `research/proposer.py` — Opus/Claude API client that reads source +
  recent traces + results.tsv, proposes mutations.
- `research/kernel.py` — already in place; needs trace capture
  extension (this task).
- `research/experimenter.py` — already in place; needs `--proposer
  opus` mode (this task).
- `research/query_traces.py` — small CLI for inspecting prior
  candidates ("show best by metric", "diff candidate A vs B",
  "show failures with >N timeouts").

**First search loop:**
1. Warm-start: seed `config_best.toml` with current production
   config (admiral knobs that `~/.drydock/config.toml` + admiral
   defaults use).
2. Run 5 random-search candidates to sanity-check the kernel
   produces stable metrics.
3. Switch experimenter to `--proposer opus`. First pass: contrast
   top-3 vs bottom-3 results, propose one diff per round.
4. Continue until random ceiling is clearly exceeded.

## Evaluation Plan

**Search set:** `scripts/stress_prompts_tool_agent_2000.txt` head-50
and `scripts/stress_prompts_realuser.txt` head-50. Combined into a
fixed `research/search_prompts_50.txt` to keep comparability.

**Held-out test:** Tail-50 prompts from each file, never shown to
the proposer during search. Only used for final validation of the
promoted winner before shipping to `~/.drydock/config.toml` or
`gemma4.md`. `research/holdout_prompts_50.txt`.

**Primary metric:**
```
effective_rate = done_per_minute with cliff at >50% skip+timeout rate
```
(already defined in `kernel.py::run_kernel`). Higher is better.

**Secondary metrics:**
- `recycles_per_100_prompts` — how often admiral had to force-recycle
  the TUI. Lower is better.
- `rss_peak_mb` — pexpect buffer health proxy.
- `raw_markdown_hits` — from the rec-check diagnostic; non-zero
  means TUI rendering leaked.
- `avg_assistant_ratio` — `assistant_msgs / user_msgs` in session
  log. Healthy range 1.5–3.5; high ratios indicate Continue-loops
  or multi-turn runaway.

**Noise:** moderate. vLLM latency varies by GPU load, Gemma 4
sampling has temperature-variance. **Replicate winners N=3 times**
before promoting — see ONBOARDING.md's "how noisy" question. Single
sample gates an experiment into a "short-list"; a median-of-3 gate
is required to overwrite `config_best.toml`.

**Per-candidate runtime:** 20 min mini-stress, hard-capped. Plus
~10 s experimenter overhead. ~20.5 min/candidate round-trip.

**Leakage / contamination risks:**
- Prompts in the search set will be seen by the proposer via
  traces. Prompts in the held-out set will NOT. Proposer context
  filter enforces this.
- The mini_prd.md package bootstrap happens once; repeated runs
  against the same pre-built package are fine because admiral
  knobs + prompts don't depend on file contents.
- `drydock/admiral/persistence.py` caches tuning across runs; the
  kernel's isolated HOME already addresses this (each run writes
  its own `admiral_tuning.json`).

## Baselines

**Obvious hand-written baselines:**
1. **Current production config:** `~/.drydock/config.toml` as checked
   into `DEPLOYMENT.md` (2026-04-20). System prompt `ralph`, temp 0.2,
   thinking high, per_prompt_budget_sec 300, hard_stop 100,
   wrap_up_warn_at 40, stop_now_warn_at 60.
2. **`research/config_base.toml` defaults:** the explicit mutation
   surface's defaults. Matches production within a point or two.
3. **"Continue-loop disabled":** flip `DRYDOCK_AUTO_CONTINUE_DISABLE=1`
   for comparison. Known to help text-only prompts.
4. **"Lower temperature":** temperature 0.1. Gemma 4's tool-call
   reliability improves at low temp; hypothesis worth baselining.

**Strongest current harness:** Production config + 2026-04-20 fixes
(aafa090 should_break_loop, 11cfe89 empty-result detector, todo tool
escalation). This is the beat-target.

**Reusable helper functions available from the start:**
- `research/kernel.py::run_kernel` — isolated-HOME stress runner.
- `research/kernel.py::_looks_empty` (Check 0 in admiral) — empty-
  pattern matcher.
- `scripts/stress_shakedown.py::_count_raw_markdown_leakage` — TUI
  rendering health check.
- `drydock/admiral/persistence.py::TUNING_PATH` — admiral knob
  store (isolated per kernel via HOME override).

## Experience and Logging

**Offline warm-start experience:**
- `~/.vibe/logs/session/session_*/messages.jsonl` — every real
  drydock session from 2026-03 onward. ~9300 sessions. Used by the
  proposer to ground "what failure modes actually occur" context.
- `~/.drydock/logs/admiral_history.log` — admiral intervention
  history. Long-form trace of every stress-alert + stress-action.
- `/tmp/stress_2000_v10*.log` — recent stress harness logs with
  ~800 prompts of first-run progress + hourly babysitter restart
  data.
- `CLAUDE.md` — this project's design-decision log; proposer
  should read it before proposing anything destabilizing. Already
  describes why `agent_loop.py` is frozen, why thinking is
  adaptive, why non-streaming for Gemma 4, etc.

**Encoded references for proposer context:**
- `DEPLOYMENT.md` — known-working config baseline.
- `research/domain_spec.md` — this file.
- `drydock/core/agent_loop.py` (read-only) — the proposer must know
  the loop structure to avoid proposing mutations that would trip
  invariants.

**Per-candidate online experience:**
- `research/traces/<exp_id>/messages.jsonl` — session log
  copy at run end.
- `research/traces/<exp_id>/tui.log` — pexpect PTY output.
- `research/traces/<exp_id>/rec_check.jsonl` — extracted rec-check
  lines + raw_md counts.
- `research/traces/<exp_id>/summary.json` — metric, counts, config
  hash, git commit, elapsed, note.

**Metadata preserved:**
- Full mutation diff from `config_best.toml`.
- Git commit of drydock tree at kernel-invoke time.
- Wall-clock start/end timestamps.
- vLLM reachability ping before and after.

**Directory structure:**
```
research/
├── README.md
├── domain_spec.md          ← this file
├── config_base.toml        ← frozen baseline + mutation surface
├── config_best.toml        ← current best, experimenter-managed
├── results.tsv             ← append-only experiment log
├── kernel.py               ← fixed 5-min runner
├── experimenter.py         ← search orchestrator
├── proposer.py             ← NEW: Opus-backed proposer
├── query_traces.py         ← NEW: trace inspection CLI
├── mini_prd.md
├── mini_prompts.txt
├── search_prompts_50.txt   ← NEW: search-set
├── holdout_prompts_50.txt  ← NEW: held-out
├── candidates/             ← NEW: promoted variants archive
│   └── <exp_id>/
│       ├── config.toml
│       ├── gemma4.md        (if mutated)
│       └── summary.json
├── staged/                 ← in-flight variants (experimenter writes)
└── traces/                 ← per-run artifacts
    └── <exp_id>/
        ├── messages.jsonl
        ├── tui.log
        ├── rec_check.jsonl
        └── summary.json
```

**Query CLI (`query_traces.py`) — planned commands:**
- `best` — show top-5 candidates by metric
- `diff <exp_id_a> <exp_id_b>` — show config + mutation diff
- `failures --min-timeouts 3` — list runs with >N timeouts
- `context --top 3 --bottom 3` — produce proposer context bundle

## Open Questions and Unknowns

- **Opus API budget.** We haven't measured per-proposal token cost.
  Estimate: ~40k input (source + traces) + ~2k output. At Opus
  pricing that's ~$0.75/proposal. 30 candidates/day → ~$22.50/day.
  Should we cap at $10/day? (Easy to add.)
- **Replication strategy for the cliff.** Current metric has a binary
  cliff at 50% failure. Replicating a near-cliff candidate 3 times
  can produce high variance. May need a continuous-decay penalty
  instead of a hard cliff. Revisit after first 20 candidates.
- **Held-out leakage via the proposer's own context.** If the
  proposer reads `CLAUDE.md` and `CLAUDE.md` indirectly describes
  the held-out prompts (it currently doesn't, but future edits
  might), leakage risk emerges. Policy: held-out prompts never
  appear in any file the proposer reads.
- **Unfreezing `scripts/stress_shakedown.py` fully.** Currently only
  threshold constants are mutable. If the proposer argues for a
  deeper change (e.g., a new recovery primitive), do we allow it?
  Default: no; require human review + manual merge. `unknown` for
  now.
- **What happens to `config_best.toml` if the proposer proposes a
  diff that doesn't apply cleanly.** Default: drop the proposal,
  log rejection, don't penalize the proposer's "score." `unknown`
  whether we want to feed rejection back to the proposer as a
  learning signal.

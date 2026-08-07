# Drydock v3 — Session Resume / Handoff

The work runs on the workstation; you steer it remotely via Claude Code's
Remote Control.

- **Resume the actual conversation (full context):** on the workstation,
  `cd /data3/drydock-v3 && claude --resume "drydock"` (or `claude --continue`).
  History lives in `~/.claude/projects/…` and comes back intact.
- **Control it from a phone/laptop:** enable once via `claude` → `/config` →
  "Enable Remote Control for all sessions" → true; then a resumed session shows
  up at claude.ai/code (or the Claude app → Code). Execution stays on this box;
  the remote device is just the UI. Needs a claude.ai subscription + `/login`
  (API keys don't work); the box must stay on with the `claude` process running.

- **Keep it alive untended — run it in `tmux`** so closing the terminal or an
  SSH drop doesn't kill the `claude` process:
  ```bash
  tmux new -s dd                                   # start a named session
  cd /data3/drydock-v3 && claude --resume "drydock"   # remote control auto-on if enabled
  # detach (leave it running):  Ctrl-b then d
  # later, reattach from an SSH login:  tmux attach -t dd
  ```
  Detaching keeps `claude` running, so the phone/web link stays live. Only an
  actual reboot or `tmux kill-session` stops it — and even then your code is
  committed/pushed and the conversation is resumable, so nothing is lost.

> **Nothing here is lost by stopping.** Code = committed + pushed to the private
> repo. Conversation = saved under `~/.claude/projects/…`, replayed by
> `claude --resume`. Only the live remote-control *link* needs the process up;
> restart `claude --resume` to get a fresh one.

This file is the **fallback primer** for starting a brand-new session without
that history — paste it as the first message.

---

## What this is

Drydock v3 is a **clean-room, Apache-2.0, provider-agnostic terminal coding
agent** for local LLMs. It is NOT a fork — it replaces the v2 line (a
`mistral-vibe` fork whose inherited phone-home code got the PyPI account
quarantined). The whole point of v3 is clean IP provenance owned end to end.

- **Repo:** `https://github.com/fbobe321/drydock-v3` (PRIVATE), branch `master`.
- **Checkout:** `/data3/drydock-v3` on the workstation.
- **Primary model (UPDATED 2026-06-22):** **Gemma-4-31B dense** (QAT
  `Q4_K_XL`, NOT the 26B-A4B MoE) served by llama.cpp at
  `http://localhost:8000/v1` (model name `gemma4`). Swapped because the
  26B-A4B's ~4B active params caused fatal agentic loops (180× identical
  pytest). The dense 31B is loop-free; ~3–4× slower (15 tok/s tensor-split
  vs 64) but it FINISHES. Vision via matching `mmproj-gemma4-31b-F16.gguf`.

---
## 🏝️ WEEK-LONG SELF-DISTILLATION RUN — 2026-07-27 08:55 (operator on a 1-week trip)

Operator left for a week; asked for a **large unattended job that keeps running through SSH
drop / crash / reboot** to *really test self-distillation* (traces + finetunes + eval, nohup +
crons). Built a **closed-loop, gated self-distillation orchestrator**. **Nothing here needs the
operator.** Full guide + running log: **`/data3/tbench_local/frontier/selfdistill/README_RUN.md`**.

**One-command status:** `bash /data3/tbench_local/frontier/selfdistill/selfdistill_digest.sh`

> **🧬 2026-08-07 — REFRAME: the ratchet is evolution with the HEREDITY WIRE CUT. Pivot to the write-back loop.**
> Operator insight ("this process created all of life, we're missing something"): evolution = variation +
> selection + **HEREDITY**, iterated at scale. Our ratchet has variation (LLM attempts) + selection (checker)
> but **severs heredity** — every task restarts from the identical, unchanging base; the solves the ratchet
> earns NEVER improve the model, so nothing compounds. That's why the eratchet stopped improving: we built an
> evolution that can't reproduce. Secondary misses too: fitness is a **plateau-and-cliff** (coarse binary
> checks, no gradient through intermediates), **homeopathic scale** (~3 tasks/8 rounds), **broken recombination**
> (filesystem-diff crossover yields dead offspring), **greedy rollback kills stepping-stones** (no neutral
> drift), and an **intelligent operator collapses diversity** (correlated attempts vs blind mutation).
> **⇒ DECISION: drop the evolutionary escalation, restore HEREDITY = the self-distillation write-back.** The
> "substrate that reproduces" is the MODEL WEIGHTS; the ratchet's verified solves are the beneficial mutations;
> self-distillation training is the inheritance that compounds. **DONE THIS TURN (no GPU — control owns .22):**
> assembled the **heredity corpus** = 16 verified ratchet solve trajectories → all 16 CONDENSED cleanly (raw✗→
> condensed✓ recipe) → `heredity/heredity_sft.jsonl` (16 records, **2× the 7-trace corpus that stalled the
> original run**). Speciation gave 1 cluster (condensed text too homogeneous — cluster on the INSTRUCTION next).
> **NEXT (needs GPU, after control frees .22):** train a LoRA on the heredity corpus (matched HF-Q5) → eval the
> FROZEN held-out set → does inheriting the solves make the model GENERALIZE (heldout_solves>0)? That is the real
> mission experiment; the ratchet was always just the collector. **2nd lever (design):** finer-grained fitness
> (sub-goal / partial-credit rewards) so within-task selection has a gradient to climb.
> **Preliminary from the CONTROL:** ~5/9 of solved tasks re-solve single-attempt (~55% base one-shot) — early.
> See [[project_compass_selfdistill_verdict]], [[project_two_box_ratchet_infra]].

> **🧪 2026-08-07 — ESCALATION PIPELINE (low→high across 2 boxes) + a CONTROL to measure ratchet value.**
> **Effort spectrum operationalized as a cheap-triage→expensive-escalation pipeline:** `.22` (llama.cpp) runs
> LOW-effort plain-pawl ratchet triage; `.21` (vLLM, 2 GPUs after its own llama.cpp was stopped to end CUDA
> OOM) runs HIGH-effort eratchet on the residue. **tbench = 29/89 (32.6%).**
> **Two critical bugs found + fixed this session** (both had been silently corrupting results):
> 1. `commit_ref` `set -u` bug → eratchet only EVER ran round 1 (earlier "evolutionary" results were bogus
>    round-1 base one-shots). Fixed (split the `local`); smoke-test strengthened to require a round-2+ line.
> 2. **vLLM EngineCore CUDA-OOM crash** on drydock's full request (system+~12 tools) — a 500 that took the whole
>    server DOWN. Root cause was OOM (vLLM contending with llama.cpp on the .21 box). **Operator fixed it**
>    (stopped .21's llama.cpp → vLLM owns both GPUs; OOM 74→0, 500s 19→0, 17× headroom). Verified: drydock agent
>    now ENGAGES on vLLM (ctx grows, real ~20min drives) and vLLM held stable across a full overnight session.
> **RESULTS SO FAR (honest):** `.22` low triage DONE (68/68, **15 solved** — plain pawl harvested the easy
> wins). `.21` high eratchet now runs the FULL ladder for real (diversify→fan-out×3→**crossover fired**, e.g.
> overfull-hbox 2/4→3/4) — BUT so far only MATCHES plain-ratchet bests (video-processing 3/5, overfull 3/4);
> **no crack the plain ratchet couldn't get yet.** Cost flag: high-effort fan-out rounds are ~3× slower
> (3 variants × 20min ≈ 1hr/round). Genuine plain-ratchet across-rounds cracks remain the only proven value:
> llm-inference(r6), bn-fit(r2), sam-cell(r3), fix-code-vuln(r4), prove-plus-comm(r4).
> **🔬 CONTROL RUNNING (operator's key question):** all 29 solved tasks re-run through `.22` at SINGLE-attempt
> (`control_noratchet.sh`, NO rounds/snapshot/rollback) to measure how many the model+harness solve on their
> own → **ratchet's marginal value = 29 − control_solves.** Master: `control/control_results.csv`. 2 streams,
> ~4-5h. This number is the real verdict on whether the ratchet is worth it. [[project_two_box_ratchet_infra]]

> **🔴 2026-08-06 (late) — CORRECTION: the eratchet (evolutionary ratchet) was BROKEN the whole time; its
> "results" were round-1-only.** Bug: `commit_ref(){ local g="$1" ref="...${g}..."; }` in `ratchet_evolve.sh`
> — under `set -u`, `g` is unbound when `ref` is expanded (same `local` statement), so commit_ref returned
> empty → `restore_ref` got "none" → **every round after round 1 silently `restore FAILED`**. So the eratchet
> NEVER ran its evolutionary rounds (fan-out/crossover/diversify never executed). **⇒ the earlier "eratchet's
> FIRST solve merge-diff-arc-agi-task 5/5 (crossover cracked it)" was WRONG — it was a round-1 base one-shot.**
> Same for the other eratchet "results" (video 4/5, overfull 2/4, caffe 1/6): all round-1-only. FIXED (split
> the `local`); strengthened the queue smoke-test to require a round-2+ line, not just a "DONE".
> **What still stands (REAL):** the PLAIN ratchet (`ratchet_solve.sh`, no such bug — uses a simple `$SNAP`)
> genuinely cracks tasks across rounds. **tbench solved = 24/89 (27%)** = base 10 + plain-ratchet, with **5
> genuine across-rounds cracks**: llm-inference(r6), bn-fit-modify(r2), sam-cell-seg(r3), fix-code-vulnerability(r4),
> prove-plus-comm(r4). The evolutionary ratchet has contributed **0** verified cracks (it never ran).
> **2ND OPEN BUG (unfixed): on the vLLM box (.21) the agent NO-OPS** — with restore fixed, rounds now run but
> the drydock-3.1.7-on-vLLM agent completes each round in ~35s with `ctx 0/65k` (does nothing), vs a real ~20min
> drive on .22/llama.cpp. So the `.21` high lane is parked until that's diagnosed (empty-response path despite the
> skip_special_tokens fix? a 3.1.7 regression? the drive prompt not landing?). **CURRENT PIPELINE:** `.22` low
> ratchet (plain pawl, 3 rounds) over unattempted candidates = WORKING; `.21` high = STOPPED pending the vLLM fix.
> [[project_two_box_ratchet_infra]]

> **📊 2026-08-06 — POOL SWEEP + eratchet-LIVE + 2ND SERVER; tbench ≈ 25/89 (28%).** Measuring the ratchet's
> reach across the pool with THREE parallel ratchet workloads (see live status via heartbeats).
> **THE HONEST HEADLINE:** ~28% solved, but attribution matters — MOST new solves are ROUND-1 base one-shots,
> i.e. `base_solved.txt` (10) badly undercounts the base's real one-shot ability when driven through the REAL
> TUI + research nudge. **Genuine ratchet-EARNED (needed >1 round of cumulative selection): 6** —
> old ratchet: llm-inference-batching-scheduler(r6), bn-fit-modify(r2), sam-cell-seg(r3), fix-code-vulnerability(r4),
> prove-plus-comm(r4); **eratchet (evolutionary, FIRST solve): merge-diff-arc-agi-task 5/5** (old ratchet stalled
> at 3/5 — crossover/fan-out cracked it). So ~28% is "base + a good agent harness"; the ratchet mechanism itself
> has earned 6. Pool sweep master: `ratchet/ratchet_pool_results.csv`.
> **INFRA built this session:**
> - **2-stream parallel pool sweep** (`sd_train_oneshot_ratchet_pool2.sh`): per-task results read from each
>   task's own log (race-free), master appended under `flock`; candidates interleaved A/B so slow gradient-rich
>   tasks split evenly; resumable (skips recorded). `ratchet_solve.sh` gained `ABORT_FLAT_ROUNDS` (bail no-gradient
>   tasks early).
> - **eratchet running LIVE** (`sd_train_oneshot_eratchet_queue.sh` with `SKIP_WAIT=1`) on the gradient-stuck
>   tasks the old ratchet couldn't close. Mixed so far: merge-diff SOLVED, video-processing 3/5→4/5 (improved),
>   overfull-hbox 3/4→2/4 (WORSE) — evolutionary ≠ strictly better; it's earning select hard cracks.
> - **2ND SERVER: vLLM Gemma-4-31B @ `192.168.50.21:8000`** as an ADDITIVE 3rd stream (operator: "a second
>   server already doubles our work"). ~2× faster/request but ROUND-BUDGET-BOUND (each round runs to the fixed
>   1200s), so the win is the parallel lane, not lower wall-clock. Delivered the patched drydock into its
>   containers via **`DD_WHEEL`** (new env in `tui_task_lib.sh` — install a local wheel instead of the PyPI pin;
>   backward-compatible). `.22`=llama.cpp (stream A + eratchet), `.21`=vLLM (resumes stream B's 22 candidates).
> - **`drydock/providers.py` fix + SHIPPED**: send `skip_special_tokens: false` for vLLM (empty-content-on-
>   truncation would score as a checker FAILURE). **Published PyPI 3.1.7** (fix + serving docs) and **deployed
>   the website** (drydock.pages.dev) with llama.cpp + vLLM serving recipes; README "Serving Gemma-4-31B" section.
> Naming: operator calls the evolutionary ratchet **"eratchet"**. [[project_selfdistill_week_run]]

> **🖥️ 2026-08-06 — 2ND BACKEND: vLLM Gemma-4-31B at `192.168.50.21:8000` (faster, but quirks).** Operator
> offered a faster vLLM box. Validated from `.22`: `gemma4` present, `/props` 404s (llama.cpp-only; drydock
> already falls back `/props`→`/models` for ctx, harmless). **Landed the load-bearing provider fix**
> (`drydock/providers.py`): for `provider==vllm`, send `extra_body={"skip_special_tokens": false}` (+ floor an
> already-tiny max_tokens to 2000). WHY: without it a turn truncated at max_tokens (tool-call/reasoning tails =
> special tokens) returns EMPTY content, which a checker-scored ratchet reads as a **failure** → silent data
> corruption. (Operator's own correction: skip_special_tokens is the real fix, not the reasoning-parser change;
> the tool parser triggers the same empty independently.) Compiles/lint/imports clean; running llama.cpp work
> UNTOUCHED (ddt containers pin published `DD_VER=3.0.138`, so source edits don't reach them). **Constraints:**
> only **2 concurrent** (`max_num_seqs=2`, a regression vs llama.cpp's 8 slots) → vLLM adds throughput as
> SEPARATE hardware, not more parallelism. `/props` poller at `.23` is a DIFFERENT host — can't remove from `.22`.
> **BLOCKER to actually use it:** containers `uv tool install drydock-cli==$DD_VER` from PyPI, so the patch must
> be DELIVERED (publish a new PyPI ver + bump DD_VER, OR install a local wheel in a stream variant) + point
> `LLM_URL=http://192.168.50.21:8000/v1`. Delivery method = open decision. See [[project_pypi_publishing_live]].

> **🧬 2026-08-06 — EVOLUTIONARY UPGRADE: all 6 gaps vs the Evolutionary Software Framework implemented.**
> Diagnosis (operator applied Information→Replication→Variation→Selection): our ratchet was a degenerate
> **(1+1) hill-climber** (population 1, asexual, single-objective) and self-distill's variation operator is
> **lethal** (packs many solutions → memorizes, transfer→0 = composition wall). Built the fixes:
> - **`drydock/ratchet.py` evolutionary core (stdlib, 42 unit tests green)** — recs 1-4,6:
>   `Candidate` (lineage: id/parents/generation/fitness-history/cost/generalizes) · `Archive`
>   (**Quality-Diversity**: one elite per behaviour niche keyed by *which* checks pass via
>   `parse_descriptor`, falls back to a count bucket) · `dominates`/`pareto_front` (**multi-objective**:
>   checks, generalization, −cost) · `VariationPolicy` (escalates on stall
>   exploit→diversify→fan-out(λ)→crossover→restart) · `plan_crossover` + `Archive.complementary_pairs`
>   (**splice two partial solutions** whose passing-checks union exceeds either — the evolutionary cure for
>   the composition wall, in environment-space where it already works) · `diversify_prompt`.
> - **Rec 5 speciation** — `research/selfdistill/speciate.py` (stdlib TF-IDF cosine + greedy agglomerative):
>   cluster solved traces into skill "species" → train ONE adapter PER cluster (not one lethal packed adapter).
>   Verified: nginx tasks co-cluster, pytorch tasks co-cluster, git separate.
> - **Live wiring** — `research/selfdistill/evolve_bridge.py` (stateful decision seam; imports the tested core;
>   persists archive+lineage+policy across rounds) + `ratchet_evolve.sh` (docker/TUI glue: QD snapshot archive,
>   fan-out best-of-λ, crossover by grafting a donor's `/app` into the base container at `/app_donor`).
>   **Bridge verified OFFLINE end-to-end** (replayed the 5/6→6/6 stall-then-escalate, and a complementary-pair
>   crossover plan) — no GPU needed. **⚠️ `ratchet_evolve.sh`'s docker/TUI path is LIVE-UNVALIDATED** — one
>   GPU smoke run pending (deferred so the pool sweep keeps the GPU); algorithms are unit-tested, glue isn't.
> - **What's protected (doing right):** objective hidden anti-cheat verifier + anti-backslip pawl.
> **Vendored** token-scrubbed to `research/selfdistill/`. **NEXT:** after the pool sweep frees the GPU,
> smoke-test `ratchet_evolve.sh` on a known staller (llm-inference 5/6) + a crossover case; then wire the
> QD/crossover mode into the live pool. See [[project_compass_selfdistill_verdict]].

> **🛠️ 2026-08-05 — RATCHET IS NOW A NATIVE DRYDOCK FEATURE: `/ratchet` (spike shipped).** The research
> ratchet proved out (llm-inference-batching-scheduler solved 6/6 across rounds; see below), so per operator
> we productized the mechanism into drydock itself. **Reframing that drove the design:** the ratchet is the
> ATOMIC primitive; self-distillation is just the ratchet at scale + a training tail — so build the primitive
> first and let a future `/selfd` compose it. **New stdlib module `drydock/ratchet.py`** (pure, no TUI imports,
> 22 unit tests green) with three primitives generalized off the tbench crutches:
> - **`Verifier`** — replaces the hidden tbench checker with the USER'S command; `score_output()` maps output→
>   `(passed,total)`: `auto` parses pytest/cargo/jest summaries, `exitcode` is all-or-nothing, or a custom regex.
> - **`GitCheckpoint`** — replaces `docker commit` with git plumbing: `snapshot()` writes a dangling commit
>   capturing tracked+untracked via a TEMP index (never touches the user's HEAD/staging/branch); `restore()`
>   rewrites the worktree and prunes files added since. Round-trip tested (modify+add+delete → restore).
> - **`RatchetState`** — the pawl: `best_passed` starts at `-1` so round 1 always locks in; `record()` returns
>   `solved|pawl|rollback`; monotonic best. `continuation_prompt()` = "work PRESERVED, N/M pass, advance w/o breaking".
> **TUI wiring** (`drydock/tui/app.py`): `/ratchet <goal> --verify "<cmd>" [--rounds N] [--fitness M]` →
> `_cmd_ratchet` sets `self._ratchet`, `_begin(goal)`; `on_agent_finished` runs `_ratchet_step` in a worker
> thread (verify→snapshot→pawl/rollback→continue or finish) mirroring `ratchet_solve.sh`; Esc/stop clears it;
> requires a git work tree (messages "git init" if absent). Added to `/help` + README (feature bullet + command
> table). **⚠️ TUI path needs the operator's HANDS-ON test** (per the TUI-testing rule — the pure module is
> unit-verified, but the in-session drive/verify/rollback loop hasn't been run through the live TUI yet). Spike
> limitation: a user prompt submitted DURING the verify window (brief, `_busy` False) could start a concurrent
> turn — fine for a spike, tighten before GA.
> **UX follow-up (operator: "too hard to use"):** `--verify` is now OPTIONAL — `detect_verifier(cwd)` auto-picks
> the command from project markers (Cargo.toml→`cargo test`; go.mod→`go test ./...`; pytest/pyproject/tests→
> `pytest -q`; package.json test script→`npm test --silent`, skipping npm's placeholder; Makefile `test:`→
> `make test`). Common case is just **`/ratchet <goal>`**; startup line shows the detected command + "(auto-
> detected — override with --verify)"; if detection fails it asks for `--verify`. 30 pure tests green (incl. 6
> detection cases). **NEXT:** operator TUI-test `/ratchet`; then `/selfd`
> collect+condense reusing these primitives (training stays an optional extra — crosses the stdlib line).
> Files: `drydock/ratchet.py`, `tests/test_ratchet.py`, `drydock/tui/app.py`, `README.md`.

> **⚙️ 2026-08-05 — RATCHET (Dawkins cumulative selection) built + under test; normal loop STOPPED
> at operator request.** The volume ceiling (stuck at 7 condensed traces) is a COLLECTION limit: best-of-N
> gives the base a FRESH container per attempt → full backslip every try → tasks needing more than one
> attempt's work are uncrackable no matter the N. **`ratchet_solve.sh`** applies a pawl:
> fitness = the REAL checker's pass-count (CTRF `passed/total`, graded — not binary); on an IMPROVEMENT it
> `docker commit`s the container (PAWL, locks in forward progress); on no-gain it discards and `restore_snapshot`
> rolls back to best next round; continuation prompt tells the model its work is preserved + current pass-count.
> **Anti-cheat:** the checker is copied in only to score, then `/tests`+`/logs` are `rm -rf`'d BEFORE the
> snapshot, so persisted state never contains the tests (a legitimate RL-style reward). Same base model, NO
> stronger teacher — mechanism, not model. **First sweep** (`sd_train_oneshot_ratchet_run.sh`,
> kv-store-grpc + nginx-request-logging): both SOLVED at round 1 (7/7, 8/8) — POSITIVE but INCONCLUSIVE, since
> a round-1 solve never exercises the across-rounds pawl/rollback. **Operator (2026-08-05): "stop the normal
> run, progress further with the ratchet."** Loop stopped by specific PID (never a broad `drydock` pkill);
> base HF-Q5 server left up; running **`sd_train_oneshot_ratchet_hard.sh`** (a NO-RELAUNCH variant — named
> `sd_train_oneshot*` so the `*/15` watchdog defers) on 3 genuinely hard, richly-graded, non-held-out/non-
> regression tasks: **db-wal-recovery, llm-inference-batching-scheduler, compile-compcert** (6 rounds each,
> 1200s/round). db-wal-recovery round 1 = **0/7** — the first genuinely-hard round-1 MISS, so the across-rounds
> path is finally exercising; real signal = `best` climbing ABOVE 0 in `ratchet/ratchet_hard_results.csv`.
> **Known wrinkle:** `best` starts at the `-1` sentinel, so a 0/N round-1 gets snapshotted as an "improvement"
> (harmless — the model just continues from a 0-progress container; only best>0 is real forward motion).
> Completion notice armed BOTH ways: durable Telegram (`tg_notify_ratchet_hard.sh`, detached, fires on
> process-exit) + in-app push. **Normal self-distill loop is intentionally left DOWN** (the watchdog would
> resume it ~15min after the sweep unless the cron is paused). Vendored token-scrubbed to
> `research/selfdistill/` (ratchet_solve.sh, sd_train_oneshot_ratchet_{run,hard}.sh, tg_notify_ratchet*.sh).
> **🏁 HARD SWEEP RESULT (final):** db-wal-recovery **0/7 flat** all 6 rounds (no partial-credit gradient →
> nothing for the pawl to grab); **llm-inference-batching-scheduler 🎉 SOLVED 6/6 at round 6** — sat at 5/6
> for rounds 1–5 (base can't one-shot the last check), the pawl HELD 5/6 across five failed rounds without
> backslip, round 6 closed it → **first genuine across-rounds crack best-of-N structurally can't do**;
> compile-compcert **0/3 flat**. **Lesson: the ratchet lifts tasks WITH a gradient, not tasks the base is
> totally stuck on.** Solved traj saved (`ratchet/llm-inference-batching-scheduler_solved_traj.json`).
> Watchdog + `@reboot` cron PAUSED (`[RATCHET-PAUSE]`, backup `crontab-preratchet-2026-08-05.bak`) to keep the
> loop down for the productization work → see the `/ratchet` native-feature block above.
> **On resume read `ratchet/ratchet_hard_results.csv` + `ratchet/*.log` FIRST.** [[project_selfdistill_week_run]]

> **🌙 OVERNIGHT AUTONOMOUS 2026-08-01 (operator asleep, "process all the todo"):** **🏁 FORK 1 DONE —
> composition wall DEFINITIVE: multi-task reproduction = 0/21** (0/3 on every one of the 7 trained tasks;
> the earlier 2/7 was NOISE — break-filter 0/3 here). `multirun_results.csv`. Single-task reproduction is
> robust; packing many solutions into one adapter memorizes (loss→0) yet retrieves NOTHING — rank/pruning
> don't help. **The memorize-many-solutions path is DEAD; only bet left = GENERALIZATION via corpus VOLUME
> (P2).** **P4 DONE + ENABLED:** `corpus_rag.py` (stdlib retrieval) injects
> the most-similar PAST solve as an analogical hint into the research assist (RSI compounding) — wired in
> `frontier_collect_n3.sh` (`CORPUS_RAG`, graceful) and ON in the loop (`CORPUS_RAG=1`); never retrieves the
> current/held-out task; harvest still keyed to the plain instruction (no contamination). **Loop eval MADE
> VALID:** `serve()` now conditional — QAT 65536/np2 for base-only COLLECT, MATCHED HF-Q5 32768/np1 when a
> LoRA is applied (a HF-trained LoRA is inert on QAT); `serve hfq5` + `measure_baseline` now measure
> held-out+regression on HF-Q5 (no `BASE_HELDOUT=0` assumption); baseline.env invalidated. After fork 1 the
> loop resumes P2 collection with all of it live. **P2/P3 now proceed automatically as the corpus grows.**
> **VENDORED to GitHub 2026-08-01:** the self-distill research harness (was non-git `tbench_local`) is now
> backed up under `research/` in this repo — `research/selfdistill/` (orchestrator, corpus_rag.py, the
> sd_train_oneshot_* experiments, tg_notify_* with token SCRUBBED→`TG_TOKEN` env, frozen task splits) +
> `research/collector/` (frontier_collect_n3.sh, tui_task_lib.sh, general_condense.py) + `research/README.md`.
> CODE ONLY — data/logs/models/runtime-state and the canary-bearing raw corpus excluded (scrubbed condensed
> dataset is on HF). The OPERATIONAL copies still run from `/data3/tbench_local/…`; `research/` is the backup.
> **🎯 DECISION 2026-08-01: GRIND VOLUME FOR WEEKS + TEST GENERALIZATION (operator).** Memorization path is
> dead; the bet is now that GENERALIZATION emerges as a large diverse condensed corpus accrues. Loop is
> unattended + self-healing and trains+evals held-out (matched base) automatically each +2 traces (next at
> corpus 9); generalization signal = `heldout_solves>0` in `eval_results.csv`. NEW cron `selfdistill_signal.sh`
> (every 6h) Telegrams ONLY on a held-out hit / promotion / corpus milestone + a daily heartbeat — no watching
> needed. Yield ~0–1/window so it's a multi-week accrual; early eval rows null until volume is large.
> **On resume read `eval_results.csv` (held-out column) FIRST.** See PRD top, [[project_selfdistill_week_run]].

> **⚠️ 2026-07-31 19:20 — MULTI-TASK COMPOSITION WALL; P1 shipped; diagnostic running.** **P1 DONE
> (committed/pushed):** `general_condense.py` keeps the artifact-producing RUN step → run-based tasks
> (distribution-search, build-pmars) now condense to COMPLETE targets. compass `3bbb19d`; drydock-v3 pushed
> `863e34f..940e511`. **Held-out test (multi-task, 7 traces, r=32): 0/6 held-out AND 0/7 TRAINED** — the
> surprise: the multi-task LoRA reproduced NOTHING incl. break-filter (single-task=3/3), despite full
> memorization (**loss→3e-5**). Packing 7 `task→solution` maps into one r=32 adapter memorizes under
> teacher-forcing but **fails to RETRIEVE at free-generation inference** → composition/over-overfit wall.
> **⇒ P2 volume is premature until composition is cracked.** **RUNNING (verdict ~21–22:30, Telegram armed):**
> `sd_train_oneshot_composition.sh` → **VERDICT: 2/7 at r=128** (up from 0/7 at r=32): break-filter ✓,
> custom-memory-heap-crash ✓; other 5 ✗. **Rank IS a lever (0→2) but not the whole story — the split is by
> SOLUTION COMPLEXITY:** short single-file fixes reproduce; multi-file/run-heavy/computational don't (even
> with P1 run-steps + full memorization). **NEXT (operator "2 then 1"):** (2) prune noisy condensed targets
> (drop dead file-versions, keep-all when none run-referenced) → retrain r=128 → retest; (1) then orient P2
> collection to the tractable simple-fix subset + measure held-out generalization within it.
> **(2) target-pruning DONE + retest: 0/7** (`composition2_results.csv`). `general_condense` now prunes dead
> file-versions (distribution-search 9→2; break-filter keeps out.html). Retrained r=128 on pruned 7
> (loss→0.005, serve verified) → 0/7 incl. break-filter/custom (were 1 unpruned; pruned targets IDENTICAL)
> ⇒ **2/7 vs 0/7 is within-noise on a low base rate, NOT "pruning hurt".** **HONEST VERDICT: multi-task
> composition reproduces poorly (~0–2/7) regardless of rank(32→128) or pruning — NOT cracked** (single-run
> evals; single-task tests used 3 runs). ⇒ the memorize-many-solutions path is blocked; remaining bet is
> **GENERALIZATION via volume** (untested, needs P2). Next-decision options: multi-run eval to pin the true
> rate / commit P2 volume + watch for generalization / per-cluster adapters. **On resume read
> `composition2_results.csv` + `composition_results.csv` FIRST.** See PRD top, [[project_selfdistill_week_run]].

> **🚀 MISSION PHASE 2 — 2026-07-31: SCALE THE FORMULA TO SOLVE TBENCH.** The recipe is PROVEN
> (raw✗→condensed✓, break-filter 3/3). Operator: *"we have the formula — build traces in phases into a
> dataset + LoRA to complete tbench, hopefully generalizing."*
> **FORMULA (teacher-free):** verified `base✗→assist✓` TUI solve → **CONDENSE** to `task→winning file
> edits→verify` (`general_condense.py`) → QLoRA on the **MATCHED** base → reliably reproduces. Raw traces
> don't transfer; condensed do. Generalization = the volume bet.
> **PHASES:** **P1** extend `general_condense.py` to KEEP the artifact-producing run step (today drops
> `Bash`, so run-based tasks — ~½ the pool — can't condense; highest-leverage unlock). **P2** volume-collect
> condensed traces over the full 73-pool in batches (7→30→50+; yield is the master lever). **P3** retrain +
> eval frozen 6-task HELD-OUT per batch, plot held-out-solves vs corpus size (compounding curve); serve
> LoRAs on the **MATCHED HF-Q5** base (see `serve()` TODO — QAT washes the adapter out). **P4** corpus-RAG
> assist (GraphRAG over solved traces) to raise yield. **P5** publish dataset+LoRA milestones to the HF
> repos. **WIN:** held-out tbench tasks flip 0→1 as the corpus grows. **In flight now:** held-out
> generalization test at n=7 (`sd_train_oneshot_condensed_multi.sh` → `condensed_multi_results.csv`,
> Telegram armed) — feeds P3. See [[reference_hf_published_artifacts]], PRD Implementation Status top.

> **📌 UPDATE 2026-07-30 ~21:00 — gen8 first HONEST result; base-mismatch found+FALSIFIED; hard-overfit
> test RUNNING (operator engaged: "make it work, don't give up").**
> - **gen8** (first gen where every phase actually ran): `ho=0/6(base=0) reg=1/4(base=2) promoted=no` in
>   `eval_results.csv`. Loop works end-to-end; gate correctly refused a non-transferring + mildly-regressed
>   LoRA (champion stays base). BUT the 4h COLLECT folded **0 new traces** → the run is **collection-starved**
>   (absent new data, every gen retrains the same 7 traces to the same null).
> - **Base-mismatch bug found:** loop TRAINS/converts LoRAs vs `gemma-4-31B-it-hf` but SERVES on
>   `gemma-4-31B-it-qat-UD-Q4_K_XL.gguf` (different checkpoint → delta from wrong origin). Built a matched
>   base `gemma-4-31B-it-hf-Q5_K_M.gguf` (built host `llama-quantize`; f16→Q5).
> - **Reproduction test (3×3, real TUI, `sd_train_oneshot_reprotest.sh` → `reprotest_results.csv`):**
>   HFbase+LoRA ≡ HFbase-plain (0,1,0 both) → **the gen8 adapter is INERT even on the matched base** → the
>   mismatch is a real latent bug but NOT why self-distill fails. The one solve (bn-fit-modify) is a
>   base-CAPABILITY effect (HF-Q5 solves it plain; QAT can't). Adapter load verified in server logs.
>   Leading blocker now: **adapter too weak** (gen8 = 60 steps, loss 0.18, r=16 — steps were cut 150→60 to
>   AVOID memorization, the opposite of what "if it can solve it we can train it" needs).
> - **hard-overfit (raw) VERDICT 22:43: INERT 0/3** (`hardmemo_results.csv`). Overfit raw
>   distribution-search to loss→0.0005 (r=32), matched base, adapter load verified → 0,0,0. Even full
>   memorization on the matched base does NOT reproduce the agentic task ⇒ weight-space memorization ≠
>   agentic execution. Confirms PRD "memorized ≠ re-executes" — the RAW trajectory format doesn't transfer.
> - **CONDENSED test — VERDICT 01:40: 🎉 WIN, 3/3.** `sd_train_oneshot_condensed.sh` overfit the CONDENSED
>   solve of break-filter-js-from-html (task→`Write /app/out.html`→verify), matched base → 3/3 solves
>   (`condensed_results.csv`), fast. Same-task A/B: raw+LoRA=0 AND plain=0 in reprotest → **raw✗ →
>   condensed✓**. **THE FIX: the loop must CONDENSE traces before training, not train raw trajectories** —
>   explains 8 gens of null transfer. Operator hypothesis HOLDS in condensed form. Caveat: `general_condense`
>   drops Bash/run steps → fully captures FILE-WRITE solutions, not RUN-based ones.
> - **✅ (a) DONE + PUBLISHED 2026-07-31.** (a) Condensing wired into `selfdistill.sh` train() (condense
>   scrubbed raw via `general_condense.py` before harvest → SFT). Serve base kept on the proven **QAT**
>   65536/np2 for collection (a switch to the matched HF-Q5 is UNVALIDATED for VRAM and only matters when a
>   LoRA is served, which is days away given starvation) — left a TODO in `serve()`: when applying a LoRA
>   at eval/champion, use the matched `gemma-4-31B-it-hf-Q5_K_M.gguf` (conditional on `$lora_arg`); QAT
>   otherwise. baseline.env invalidated (re-measures). **PUBLISHED to HF
>   fbobe3:** adapter https://huggingface.co/fbobe3/gemma-4-31b-condensed-selfdistill-lora + dataset
>   https://huggingface.co/datasets/fbobe3/tbench-condensed-selfdistill-traces (canary-scrubbed; base
>   referenced as google/gemma-4-31b-it — verify id). See [[reference_hf_published_artifacts]]. **(c) held-out
>   generalization test = STILL OWED** (task #6; deferred per "just publish"). HF token: env only, never git.
> - **NEXT (operator: "a then c then publish"):** (a) wire condensing into the loop's TRAIN (`selfdistill.sh`
>   train() → condense before harvest); (c) `sd_train_oneshot_condensed_multi.sh` — condensed MULTI-task LoRA
>   on all 7 traces + eval the 6 HELD-OUT tasks (generalization = the real RSI test); then PUBLISH the
>   condensed LoRA to HF `fbobe3/` with a full model-card write-up. Tracked tasks #5–7. **On resume read
>   `condensed_multi_results.csv` (if present) then `condensed_results.csv`.** All experiment scripts are
>   `sd_train_oneshot*` (watchdog defers; relaunch loop on exit). Telegram: `tg_notify_*.sh`.

> **📌 UPDATE 2026-07-30 08:40 — two silent-failure bugs found & fixed; first HONEST result pending.**
> Collection worked (corpus 2→7 traces, loop self-healed 7 gens/60h) but TWO bugs silently neutered
> the point of the run: (1) **07-29 TRAIN OOM'd 14×** — `stop_server` didn't wait for VRAM release so
> `device_map=auto` crammed the 4-bit model onto one GPU; fixed (wait-for-free + seq 512 +
> expandable_segments), confirmed training splits across both cards. (2) **07-30 PATH bug** — the
> cron/watchdog-relaunched loop lacked `tmux` (in `~/miniforge3/bin`), so collection AND eval silently
> no-op'd for ~a day; gen7's eval (0/6, 0/4) was spurious (no task ran). Fixed: script now exports a
> full PATH (survives cron/@reboot); model verified driving the TUI again. Also hardened: measured
> **base baseline** for a fair regression gate, promotion requires REAL transfer, **TRAIN_STEPS 150→60**
> (gen7 memorized to loss 0.004). Reset trigger → **gen8 = first honest train→baseline→eval on the 7
> traces, ~17:40 today** (`eval_results.csv`). Expectation still: transfer likely 0 (7 memorization-prone
> traces), but now MEASURED honestly. A background watcher will report gen8's row when it lands.

**The loop (each generation):**
- **COLLECT** — 2 streams drive the REAL drydock TUI (docker) over the full tbench-2 pool
  (73 tasks = 89 − 10 base-solved − 6 held-out), gate base-plain → best-of-8 research assist →
  harvest verified `base✗→assist✓`. 8h window.
- **TRAIN** — when the trainable corpus grows ≥2 traces since last train (accumulates across
  gens) & ≥3 total: stop server → canary-scrub → `compass harvest-tbench` → QLoRA (150 steps) →
  `selfdistill-gen<N>-lora.gguf`.
- **EVAL** — serve base+LoRA, run the FROZEN 6-task held-out (transfer) + 4-task regression
  through the real TUI + real verifiers. **Not a harness** (real TUI, no `-p`).
- **PROMOTE (gated)** — LoRA becomes the champion collecting the next gen **only if** it solves
  ≥1 held-out task AND breaks no easy task AND beats the best prior. Base is always the fallback →
  worst case = a week of base-model collection (strictly ≥ the old grind).

**Design decisions (operator-confirmed this session):** closed-loop w/ gated promotion; full
89-task pool (the old grind's 49 hard-residue yielded ~0). GPU is single (2×4060Ti tensor-split)
so server(collect/eval) and training are **mutually exclusive** — one sequential loop enforces it.

**Survival:** loop = `setsid nohup` + `flock` single-instance; server = docker `--restart
unless-stopped`; **crons** = `@reboot` relaunch + `*/15` watchdog (relaunch if `loop.lock` free);
`collect()` purges orphans each gen so a relaunch can't over-subscribe `-np 2`.

**Live at handoff:** gen0 COLLECT, champion=base, corpus=2 (seeded from prior verified traces),
2 `ddt_*` TUI containers gating, both GPUs ~57%. Files under `frontier/selfdistill/`
(`selfdistill.sh` + launcher/watchdog/digest, `gens/gen<N>/`, `eval_results.csv`, `state.env`,
`champion.txt`). Old overnight grind (grind_watchdog + streams) cleanly RETIRED to free `-np 2`.

**Why (context):** verdict = self-distill is DATA-constrained (thin AMBER band, 2 traces in ~2wk)
— see memory `project_compass_selfdistill_verdict`. This run maximizes the band (full pool,
best-of-8, a week) and closes the loop honestly; if transfer never appears, that IS the finding.

**Still owed:** rotate the GitHub token embedded in the remote URL (needs operator; deferred).
Nightly-green gate (earlier today): drydock-v3 suite **856 passed, 1 skipped** in `.venv`.

---

## 🧭 SESSION HANDOFF — 2026-07-24 (Document Canvas shipped v3.1.3)

**SHIPPED: Document Canvas (v3.1.3, PyPI + GitHub main `02caaa8` + tag `v3.1.3`).** Random-access,
structure-aware editing of documents far larger than the context window — the model treats a doc
like a codebase: outline/search/read a small window → hash-guarded transactional patch → validate →
commit. `drydock/doccanvas.py` (stdlib canonical block model, stable ids + content hashes, JSON store)
+ 11 tools (DocOpen/Outline/Search/Read/Patch/Replace/Redact/Diff/Validate/Commit/Rollback) +
`document-canvas` skill + README how-to. `.md/.txt` edited in place; `.pdf/.docx` imported read-only
via `drydock.extract` → `<file>.canvas.md` sidecar. **DocRedact** = verified red-boxing (blocking
non-recoverability check). Clean pip-install verified ships it all; 867 tests green, ruff+pyright clean,
security 0 HIGH. Real-doc tested: War and Peace (~1132pg, 12k blocks) + 30MB PDF dictionary (67k blocks).

**OWED (in progress this session):** live **TUI shakedown** of the canvas (drive DocOpen→…→DocCommit
through the real tmux TUI with the model — the non-negotiable rule) and of **FIAR** (built + unit-tested
but never TUI-driven). Follow-ups (tasks #9/#11 + backlog): FIAR **KSD package building**, PDF
regeneration/OCR/binary redaction, the dedicated 4-pane canvas TUI screen, semantic search.

**Design basis:** operator's two-part spec (paste-cache `3962…`/`9b27…`) reviewed; verdict = build the
md/txt engine native (stdlib, ships in wheel), heavy-format adapters later (MCP server isolates deps).

**tbench self-distillation is PAUSED** (both boxes) to free the servers for canvas/FIAR TUI testing —
see the tbench section below + `/data3/compass/RESUME.md`. Resume: relaunch `frontier_collect_n3.sh`
N=15 streams (`/data3/tbench_local/frontier/xN_s{0..3}.txt`). Verdict-so-far: 0 AMBER across ~13 tasks
at budget 20000 → pure self-distill likely can't bust tbench; stronger-teacher (Devstral-24B, local) is
the probable path (operator chose to exhaust N=15 first).

## 🧭 SESSION HANDOFF — 2026-07-23 ~23:00 (resume in /data3/drydock-v3)

Read this block first; detail is in the dated entries below + `NIGHT_SHIFT_LOG.md`.

**DONE this session**
- **Published `drydock-cli 3.1.2` to PyPI** (https://pypi.org/project/drydock-cli/3.1.2/) and
  pushed `main` + tags `v3.1.0`, `v3.1.2` to GitHub `fbobe321/drydock`. Security gate passed
  (0 HIGH). Verified: a clean `pip install drydock-cli==3.1.2` ships the FIAR skills + tools.
- **FIAR audit-readiness vertical** (`drydock/fiar.py`, 5 tools, 4 skills, evidence-chain
  validator). See the dated entry below.
- Feal seed solved → LoRA trained (`/data3/Models/frontier-feal-lora.gguf`).

**FIRST ACTIONS next session (you'll be launched from /data3/drydock-v3)**
1. **Archive the old fork** (operator-requested): `/data3/archive_old_drydock.sh` — guarded +
   reversible; moves `/data3/drydock` → `/data3/_archive/drydock-v2-retired` and neuters its git
   remote. Memory: `project_pending_archive_old_drydock`.
2. **Read the variant-test result** (it runs detached, completes overnight):
   ```
   cat /data3/tbench_local/feal_variants/results_base.csv                       # base arm (main)
   ssh bobef@192.168.50.21 'cat /data3/tbench_local/feal_variants/results_lora.csv'  # LoRA arm
   ```
   **Preliminary (0/4 each so far): the LoRA arm is failing even `v0_base` — its OWN trained
   task's control variant.** If that holds across all 10, the finding is: the single-trace
   condensed LoRA (loss 0.0000) memorized the training sequence but does NOT reproduce the
   attack when driven fresh through the drydock TUI — a real negative, not "no transfer."
   Before concluding, DIAGNOSE the LoRA arm: does it write `/app/attack.py` at all, and is it
   the right code? (tmux into a `ddt_feal-*` container on romulus, or read a diag pane.) Rule
   out a harness cause (budget-8000 over-think, TUI not converging) vs a genuine finetune miss.

**OWED / open**
- **FIAR TUI shakedown** — built + 25 unit tests + install-verified, but NOT yet driven through
  a live TUI (servers were busy). Non-negotiable rule needs it before "tested".
- **Rotate the GitHub token** — it's embedded in the git remote URL and was printed this session.
- **Restore reasoning-budget to 20000** on main `:8000` before any harvest resumes (it's at 8000
  for the variant test).
- Version convention: **patch bumps** (next = 3.1.3), not minor-per-feature.

---
## ⭐ 2026-07-23 (later) — FIAR audit-readiness vertical (v3.1.2); variant test two-box

**FIAR vertical shipped** (tag `v3.1.2`, commit aeb0b49) — DoD/War-Dept Financial Improvement
and Audit Readiness, grounded in the real April 2017 FIAR Guidance
(`/data3/drydock/tests/fiar_guidance.pdf`, extracted + built against it). New gov-compliance
vertical beside RMF/STIG:
- `drydock/fiar.py` — stdlib engine; an "engagement" (one JSON, like a STIG .ckl) holds a
  seeded key-control-objective matrix (FBWT, P2P, PP&E, INV, CIVPAY, REIM, FR, ITGC), NFRs,
  CAPs. Models the 5 distinct FS assertions, Four Waves, FROs, numbered Key Tasks, KSDs,
  ITGCs, A-123 sustainment. CLI `python -m drydock.fiar …`.
- 5 tools (FiarControls/Control/Assess/Finding/Reconcile). 4 skills (fiar-assess, fiar-evidence,
  fiar-readiness, fiar-cap).
- **Differentiator: a deterministic EVIDENCE-CHAIN VALIDATOR** — FiarAssess REFUSES to mark a
  control effective on an incomplete chain (population→sample→source txn→authorization→
  supporting doc→system posting→GL effect→assertion). Evidence-review harness, not a checklist.
- 25 tests; 834 pass, ruff+pyright clean. **⚠️ NOT yet TUI-driven** (servers busy) — owes a
  hands-on tmux shakedown per the non-negotiable rule.

**🚨 Version convention:** patch increments (3.1.3, 3.1.4, …), NOT minor-per-feature. I
over-bumped (3.1.0 hardening rollup, then FIAR→3.2.0); operator corrected FIAR to **v3.1.2**.
See memory `feedback_version_bump_patch_increments`.

## ⭐ 2026-07-23 — v3.1.0 released; feal variant-transfer experiment underway

**v3.1.0** (tag `v3.1.0`, commit e043728) promotes the 3.0.171–178 tool-robustness +
loop-defense line to a minor release. 812 tests, ruff + pyright clean. NOT yet on PyPI —
publishing is the operator's call. Highlights folded in:
- 3.0.171 trajectory export EVERY turn (survives cutoff kills) — the flush the frontier
  harvest depends on; 3.0.172 degenerate_argument prefix-bound (650ms→49ms/call);
  3.0.173–175 wrong-type tool-arg coercion (9 local-model crash classes);
  3.0.176 JSON-arg repair for quote-heavy Write/Edit; 3.0.177 inline ghost-text (Tab);
  3.0.178 **template-enumeration loop detection** (found live: a research attempt burning
  ~30 min marching through ISO locale codes with an identical empty body every call —
  invisible to all 4 prior detectors because each call is unique).

**RSI status (see `/data3/compass/RESUME.md` + `docs/PRD.md` for the authority):** two-box
frontier harvest running (main + romulus21, 34 tasks each, N=3). **Honest yield: 0 new
verified traces in ~11h across both boxes on the hard slice** — the autonomous research
scaffold is not solving hard tasks. So the **feal variant-transfer experiment** (operator's
design) switched to a **hint-assisted seed solve** (option 1): validated 10-variant test set
at `/data3/tbench_local/feal_variants/` (memorization trap handled, all 10 checked against
the real compiled C cipher), and a hint-driven TUI solve of `feal-differential-cryptanalysis`
is in progress to produce the ONE seed trace to distill. Blocker reality: it shares the busy
single-slot :8000 server, so it's slow. ETA to a transfer-demonstrating LoRA: ~6–11h,
gated on the seed solve landing.

## ⭐ 2026-07-22 — inline ghost-text prompt suggestions + Write-quote fix (COMMITTED as 3.1.0)

Two drydock improvements surfaced while driving Compass RSI collection through the real TUI:
- **Inline ghost-text suggestions (operator ask).** The recommended next-input now renders as
  dimmed GHOST TEXT inside the prompt box (Textual 8.1.1 `TextArea.placeholder`) and is accepted
  with **Tab** — replacing the old dimmed line ABOVE the box that needed Ctrl+N. And the suggestion
  is now a bounded CONTEXTUAL model call (`suggest_reply_llm`): it reads the agent's last message and
  proposes the user's likely reply (answer its question / "continue"), ≤32 tok, async, heuristic
  fallback, config-gated `suggest_llm`. Files: `drydock/suggest.py`, `drydock/tui/app.py`,
  `drydock/tui/widgets.py`; tests `tests/test_suggest.py`. 7 + 89 tui tests green. ✅ **TMUX-VERIFIED**
  in a real TUI: ghost renders in-box, Tab accepts (typed after → appended), and the contextual reply
  works (agent "what feature?" → ghost "Add a user authentication system."). Gemma-4 gotcha fixed
  mid-verify: bounded call returned empty (reasoning-budget ate the token cap) → send
  `chat_template_kwargs={enable_thinking:false}` (~1s clean reply). NOT committed.
- **`Write` on quote-heavy content (real bug from a live session).** `_parse_tool_args` degraded
  args with unescaped inner quotes (HTML/code) to `{"_raw"}` → Write failed. `_repair_json_args`
  added (providers.py) + 4 tests. A/B: FAILED on published 3.0.138, PASSED in 1 attempt on the patched
  wheel. Both changes local/uncommitted.

## ⭐ 2026-07-20 (overnight) — probe-driven tool hardening + eval support (v3.0.172 → 3.0.175)

Operator ran the Compass first-finetune pipeline (see `/data3/compass`); the drydock
side of the night was **adversarial tool-path probing + review** (all local, NOT yet
on PyPI — operator's call to publish). **3 probe rounds (103 wrong-type-arg cases) →
9 real crashes fixed**, each the "local model sends an arg as the wrong type" class:
- **v3.0.173** `tool_bash` list/int `command` (`_as_text`).
- **v3.0.174** `tool_gitdiff` / `tool_viewimage` list `path` (`_as_str_arg`).
- **v3.0.175** `tool_task`/`tool_worker` list/int `prompt` (`_as_text`, joins multi-part);
  `tool_dispatch` bare-string char-spray + non-iterable `tasks`; `tool_stigrules`/
  `tool_stigrule` list `path`/`rule_id` (`_stig_path` central fix).
- **v3.0.172** `degenerate_argument` O(const·n) latency cliff — 650ms→49ms on a 50KB
  non-repetitive command (paid every tool call); prefix-capped. (Reviewing v3.0.167–171:
  only this one had a defect; the rest were correct.)
- Test hygiene: isolated `$HOME` in 3 non-hermetic skills/mcp tests (were reading the
  real `~/.drydock`); fixed a stale compass `encode_chat` windowed-contract test.
- **796 tests pass, ruff + pyright clean.** Probe scripts: `scratchpad/probe_tools{,2,3}.py`.
- Live overnight handoff: **`/data3/drydock-v3/NIGHT_SHIFT_LOG.md`** (gitignored).

## ⭐ 2026-07-10/12 — GOVERNED-RUNTIME PRD build-out + ML task suite + NIST skills (v3.0.116 → 3.0.133)

**Agent-Buildout PRD (`Agent_Buildout_PRD.docx`) — evolve drydock into a governed
agentic runtime: a deterministic controller governs; the model proposes.** Built the
whole core backbone INCREMENTALLY into the existing loop (no rewrite), each piece
self-contained + config-gated + tested:
- **v3.0.123 structured task state** (`task_state.py`, Epic A) — objective + acceptance
  criteria (auto-extracted) live in `TaskState` OUTSIDE the transcript, re-injected into
  the SYSTEM PROMPT every turn so they survive compaction. `task_anchor` (default on).
- **v3.0.125 verification gating** (Epic B) — a text-only "done" after editing files is
  NOT accepted until a check runs. Bounded nudges; main-task only (skips sub-agents).
- **v3.0.126 event log** (`events.py`) — durable JSONL trace per session
  (`~/.drydock/events/`): task_start, turn, tool, verification, verify_gate, plan, done.
  Append-only + defensive (never raises). `event_log` (default on).
- **v3.0.127 verification EVIDENCE** — `parse_evidence()` → pass/fail from exit code +
  pytest-style tallies. Gate now branches: never-ran→verify, FAILED→repair, PASSED→complete.
- **v3.0.128 task reconstruct + `/events`** — `reconstruct_task_state()`/`summarize()`
  rebuild a task from its trace; `/events` shows the digest.
- **v3.0.129 phase tracking** — TaskState.phase understand→implement→verify→repair→complete,
  shown in the status line.
- **v3.0.130 rolling plan** (Epic C) — `RollingPlan`: stable text-derived step ids,
  1 active + ≤4 pending, versioned, synced from the `todo` checklist, revisions logged.
- **v3.0.132 structured tool results** (7.4) — `execute_structured()` wraps a tool's
  string in a `ToolResult` (status/error_code/retryable/changed_state/exit_code/
  duration). NO tool rewritten; `execute()` still returns str (backward-compat). Rich
  metadata → event trace.
- **v3.0.131** fixed a CI pyright error (typed `AgentState.events`).

**ML TASK SUITE (operator: "build/train transformers, CNNs, LoRA/full finetune, data
prep, diagnose, pytorch, ViT, H5, JSON, LaTeX, metrics, RL").** In
`/data3/tbench_local/tasks/ml-suite/` (terminal-bench format); base image
`drydock-ml-base` (CPU torch + sklearn/h5py/transformers/peft/matplotlib/texlive).
**10 validated tasks (ref solution → reward 1)**: classification-metrics, multiclass-
metrics, h5-to-json, data-split, transformer-block, latex-report, train-cnn, debug-
training, rl-qlearning, finetune-full. Runner `ml_run.sh`. **gemma solves 9-10/10 through
the real TUI** with recipes+skills in context.
- **Found + fixed a real bug via the suite (v3.0.122):** a message starting with a file
  path (`/app/env.py …`) was parsed as a slash command → "unknown command" → instant
  fail. `is_slash_command()` now excludes path-like leading tokens. This masked 4 ML
  "failures" that were actually passes.
- ML skills shipped as USER-FACING skills (v3.0.120): `/ml-train /ml-metrics /ml-finetune
  /ml-debug /ml-rl /ml-data`; ML recipes in `recipes.py` (v3.0.117/119).
- Recommended-next-command ghost hint (v3.0.118, `suggest.py`); recipe robustness +
  headless-screenshot fast-fail (v3.0.121).

**RECIPE KB (v3.0.116)** — `recipes.py`: ~20 command-first technique recipes retrieved
by keyword into the system prompt (forensics, git-history, numpy-2.0, cert, metrics,
pytorch, LoRA, ViT, RL, LaTeX, …). Hypothesis: pass-rate needs INFORMATION not behavior.

**NIST governance skills (v3.0.133)** — reviewed a third-party cyber-skills repo
(mukul975/Anthropic-Cybersecurity-Skills); DECLINED to import (overwhelmingly OFFENSIVE
— C2/pentest/social-eng — against the no-hacking guardrail + clean-room provenance).
Instead AUTHORED fresh, defensive-only, own-copyright: **`/nist-ai-rmf`** (AI RMF 1.0:
Govern/Map/Measure/Manage + 7 trustworthiness characteristics) and **`/nist-csf`** (CSF
2.0: 6 Functions + Implementation Tiers + current→target roadmap). Complements the
existing `rmf-*`/`stig-*` families.

**594 tests, all on PyPI + GitHub, CI green. GitHub push token was rotated 2026-07-09.**

---
## ⭐ 2026-07-08 — over-think interrupt, native Windows, GraphRAG fixes, Screenshot + ACTIVATION STEERING PROVEN (v3.0.107 → 3.0.114)

🔔 **FIRST: the GitHub push token is EXPIRED** (401). PyPI publishing still works;
`git push` does not. v3.0.114 (`89dc2b9`) + any later commits are on PyPI but NOT on
GitHub. User to refresh a classic PAT (`repo` scope) into `~/.config/drydock/github_token`,
then `git push origin master`. See memory `project_reminder_github_token`.

**Shipped (all on PyPI; GitHub behind from 3.0.114):**
- **3.0.107 over-think interrupt** — escalating stall-retry → "decisive mode": on a
  persistent stall/over-think, re-issue with a forcing system suffix + max_tokens cap
  (1500) + reasoning_effort=low, so gemma physically can't burn a 5k-token no-action
  turn. Gated by stall_retry_secs. + no-pip-dependency prompt nudge (openssl lesson).
- **3.0.108-110 native Windows** — runs in PowerShell/cmd, NO WSL/bash. `_detect_shell()`
  (Windows→PowerShell else cmd; never bash), `tool_bash` builds `pwsh -NoProfile
  -NonInteractive -Command`; `DRYDOCK_SHELL` env override; bulletproof `_is_windows_env`
  (WINDIR catches MSYS/Git-Bash Pythons); `/shell` diagnostic command. Prompt tells the
  model its real shell.
- **3.0.111** — the Bash tool DISPLAYS as its real shell ("PowerShell"/"cmd") on Windows
  (schema name stays "Bash" for the model).
- **3.0.112-113 GraphRAG** — `_unquote()` strips quotes around a path (Windows "No text
  found" bug); per-file try/except so one bad doc can't crash a folder build; NEW
  **BuildKnowledge** tool so the MODEL can build the KB itself instead of punting to
  `/graphrag`.
- **3.0.114 Screenshot tool** — captures the screen to PNG and the model SEES it (auto-
  attach, same path as ViewImage). Windows PowerShell/System.Drawing, macOS screencapture,
  Linux grabbers.

**🎯 ACTIVATION STEERING PROVEN** (the operator's "hooks INTO the model, not just prompting"
goal). Full detail in memory `project_activation_steering` + `/data3/build/steer/`.
- llama-server supports `--control-vector-scaled` + `--control-vector-layer-range`; built
  `llama-cvector-generator`. v1 (12 pairs, trait words in prompt, all layers) FAILED (no
  usable window). **v2 (30 clean pairs — same task, only response differs decisive-vs-
  deliberating; layers 20-45) WORKS**: coherent at +3..+5, "count ERROR" 127→58 tok.
- **Real agentic test**: password-recovery went 0→10 tool calls steered (baseline over-
  thought 5.8k tok, no action) — but still FAILED (capability wall). Proves steering fixes
  BEHAVIORAL failures, not capability.
- **RSI measurement loop** (`/data3/tbench_local/steer_measure.sh` + `classify.py`): runs
  tbench baseline-vs-intervention through the real TUI, splits failures into behavioral
  (recoverable) vs capability-limited → the "wasted-capability slice".
- **VERDICT (5 tasks, 2026-07-09): behavioral interventions don't move pass-rate here.**
  Steering @+4: recovered **0/3** failures, **regressed 2** passing tasks. Wall-time interrupt
  (steer_measure/interrupt_measure.sh, thresholds 300 & 480): ~net-zero, reliably regressed a
  productive-reasoning task (adaptive-rejection-sampler) at both. On this set gemma's failures
  are **capability-bound, not behavioral** — all three cut over-think tokens, none reliably
  flip fail→pass. (n=5, single-run — directional.) Full numbers: `STEERING_RESULTS.md`.
- **Repetition detector (v3.0.115)** kept as the one behavioral win: `RepetitionDetected`
  fires on a genuine pure-repetition loop only (never productive reasoning) → decisive mode.
  Precise + safe (no regressions), unlike wall-time. Gated by `stall_retry_secs`.
- Apply steering: `bash /data3/build/steer/APPLY_v2.sh 4 20 45`; revert: `REVERT_main_server.sh`.
- **Next hypothesis (pass-rate = INFORMATION, not behavior):** few-shot task exemplars,
  retrieved tool recipes via GraphRAG, environment priming, requirement self-check.

## ⭐ 2026-07-06 — full tool I/O-path + arg hardening via direct probing (v3.0.93 → 3.0.103)

**Key insight this session:** *directly probing drydock's own tool paths* found
**~11 real bugs** that grinding hard tbench tasks did NOT — the hard tasks (cobol,
db-wal) run the toolchain cleanly and are model-limited, so they don't surface
harness bugs; the leverage is in probing edge cases of every tool. A whole *class*
emerged: local models send args as the wrong type (a JSON array of lines, a
stringified number, a wrapped path) → uncaught crashes. Fixes:
- **93** binary/non-UTF8 bash output → `errors="replace"` (was "(no output)" crash).
- **94** `_sanitize_bash_output()` strips ANSI + drops NUL.
- **95** partial bash output preserved on timeout (tail-bounded).
- **96** `_coerce_timeout()` — string/0/negative/absurd timeout params.
- **97** Grep surfaces grep errors (invalid regex/bad path) vs false "(no matches)";
  grep/Read binary-safe.
- **98–99** wrong-type args coerced across write/edit/read/grep. Helpers
  `_as_text` (list→newline-join), `_as_str_arg` (unwrap single-elem list),
  `_coerce_int` — the reusable coercers for this whole class.
- **100** (operator, `5bad1b1`) STOP/Esc preserves partial bash output.
- **101** Edit honors **replace_all** (was silently ignored → multi-match loop).
- **102** Glob no-crash on missing/list pattern; clear empty-old_string error.
- **103** GitCommit/Knowledge/Consult/WebFetch/WebSearch coerce wrong-type args
  (were `.strip()` AttributeErrors).

**All 23 tools now audited** for missing + wrong-type args. git/graph/web/dispatch
already validated missing args; the crash class lived in the file tools + the
`.strip()` tools (now fixed). Probing tools >> grinding hard-reasoning tbench tasks
for harness bugs. `cd` not persisting across bash calls is by design. Remaining
probe idea: multi-tool-turn flows under real LLM traffic (long bash → Edit → bash).

## ⭐ 2026-07-03 — second-model advisor + 7 tbench-through-TUI fixes + Graphify (v3.0.82 → 3.0.92)

**Graphify MCP integration (v3.0.92).** [Graphify](https://github.com/safishamsi/graphify)
(external, MIT) serves a codebase **knowledge graph** over an MCP stdio server;
drydock's existing MCP client connects with **no code changes** — validated
end-to-end through the TUI (agent autonomously called `mcp__graphify__god_nodes`).
Made permanent as docs+example, NOT code coupling: `docs/graphify.md`,
`examples/mcp/graphify.json`, README + website notes, `tests/test_graphify_example.py`.
Fully local: structural build needs no LLM; semantic build points `OPENAI_BASE_URL`
at the local gemma server. Complements drydock's own `/graphrag` ([[project_graphrag_feature]]).


PyPI **3.0.91**; HEAD pushed/green; **441 tests**. Everything below was found by
driving **real terminal-bench-2 tasks through the actual TUI** (the
`/data3/tbench_local/tui_task_lib.sh` harness: `ddt_up`/`ddt_tui`/`ddt_verify`/
`ddt_down`, gemma at `host.docker.internal:8000`) and fixing what broke — never
`-p`, never a batch judge. ~35 tasks across every category; harness had **zero
crashes**, ~40% pass (failures are the local model's capability, not drydock).

**Second-model advisor (v3.0.82–85).** Ask a stronger model (e.g. Gemini via its
OpenAI-compatible endpoint) for help without leaving the TUI. `Consult` tool +
`/advisor` (url/model/key/**test**) + `/ask <q>` (advice to screen) + **`/ask! <q>`**
(inject the advice straight into the agent's context). Config keys
`advisor_base_url`/`advisor_model`/`advisor_api_key`. No new dep (reuses `openai`).

**The 7 fixes (each a real drydock bug hands-on use surfaced, unit tests missed):**
1. **v3.0.84 `/advisor test` timeout** — 30s false-negative on a cold/slow model;
   now 90s + "REACHABLE but slow". (Found testing two live LLMs on .21/.22.)
2. **v3.0.86 stall watchdog** — the model server (llama.cpp) hangs mid-generation
   on hard prompts (token count freezes, elapsed climbs); the activity line now
   warns "no output for Ns" after 180s. Advisory only (never kills). Fired
   correctly ~8× live; silent on legit slow steps.
3. **v3.0.87 compaction on the REAL token count** — `maybe_compact` keyed on the
   chars/3.0 estimate, which undercounts token-dense build/code output, so
   compaction fired late (gauge showed 64% while estimate read ~55%). Now uses
   `max(estimate, state.last_input_tokens)` — the server's real prompt-token
   count. (Surfaced by build-cython-ext.)
4. **v3.0.88 ViewImage-over-OCR** — the model reached for tesseract/pdftotext on
   invoice/scanned images instead of its own vision; tool desc + both system
   prompts now say use ViewImage first. (Surfaced by financial-document-processor.)
5. **v3.0.89 background processes** — a naive `python server.py &` (no redirect)
   HUNG `tool_bash` to the timeout then killed the server (the backgrounded child
   inherits the stdout pipe → drain thread never sees EOF). Now: shell exited but
   pipe still held → return promptly WITHOUT killing, so the bg server survives.
   Validated live on pypi-server (`pypiserver … & sleep`) + qemu-startup (bg VM).
   (Surfaced by kv-store-grpc.)
6. **v3.0.90 stdin=DEVNULL** — `tool_bash` never set stdin, so a subprocess
   inherited the TUI's terminal; a stdin-reading command would steal keystrokes or
   hang. Now DEVNULL → instant EOF, isolated from the TUI; piped/redirect input
   still overrides it. (Found investigating interactive-stdin tasks.)
7. **v3.0.91 bash-not-dash** — `shell=True` used `/bin/sh` = dash on Debian, so
   `[[ ]]`, `<<<`, arrays, `{1..n}`, `<(…)` failed with confusing syntax errors
   the model looped on. Now resolves bash once (`_detect_bash`) and passes
   `executable=_BASH_SHELL`; falls back to /bin/sh if bash is absent.

**tbench passes worth noting:** build-pmars, build-pov-ray (render matches ref),
modernize-scientific-stack, hf-model-inference (bg Flask+HF model),
pypi-server (bg pypiserver), qemu-startup (bg Alpine VM on telnet:6665),
constraints-scheduling. Fixes 89–91 all harden `tool_bash` (server/daemon +
interactive-stdin + shell categories). Launcher gained `uv --refresh` (publish→run
PyPI propagation lag). `_BASH_SHELL`/`_detect_bash`, stall watchdog, and the bg/stdin
logic all live in `drydock/tools/__init__.py`; compaction in `drydock/compaction.py`.

---
## ⭐ 2026-06-29 (overnight) — multimodal + context diagnostics + hardening (v3.0.75 → 3.0.81)

PyPI **3.0.81**; HEAD pushed/green; website redeployed. **409 tests.**

- **Multimodal is first-class.** v3.0.77 user-attached vision (📎 confirmation +
  the model is told it has vision); v3.0.78 **agent-side `ViewImage` tool** — the
  agent can look at images it discovers (image rides back on the tool result;
  the llama.cpp server reads images from tool-role messages, verified); v3.0.80
  `Read` on an image points to `ViewImage`; v3.0.81 a server image-decode 400
  degrades to a clean message (found via TUI edge-case hunt). All TUI-verified on
  the mmproj gemma server (read "DRYDOCK 42" / "42" off PNGs).
- **`/context` server probe (v3.0.76).** Probes the model server's real `n_ctx`
  (llama.cpp `/props`, vLLM `max_model_len`) and warns if the SERVER is smaller
  than drydock's budget — the definitive "stuck at 32k" diagnostic. Verified
  against a fake 32k server: it warns correctly. (See [[feedback_context_limit_32k_trap]].)
- **eMASS POA&M CSV (v3.0.79).** `/stig poam <ckl>` → deterministic CSV of open
  findings (Control via CCI map, CAT→High/Moderate/Low, Status=Ongoing,
  Milestone=Fix Text). `drydock/poam.py`, stdlib-only.
- **Sub-agent summary cap (v3.0.75).** Dispatch/task returns are capped (~4000
  chars) so a sub-agent's work can't bloat the main context.
- **Overnight TUI bug-hunt (all clean except the corrupt-image fix above):** huge
  bash output stays ~5% ctx; git tools, Dispatch fan-out (bounded summaries),
  full 286-rule STIG chain (new→summary→graph, 265/286 CCI-linked), and a
  multi-turn build (wrote + self-verified a Stack module) all held up.
- 🚨 **Eval-harness ban REAFFIRMED 2026-06-29.** The `EVAL_HARNESS_DESIGN.md` +
  `_anthropic/INTERP_AGENT_EVAL_INTEGRATION.md` specs exist but the operator said
  **keep the ban** — do NOT build `drydock/eval/`. See [[feedback_no_custom_eval_harness]].

---
## ⭐ 2026-06-29 — STIG pipeline completion + user-reported fixes (v3.0.62 → 3.0.74)

Repo HEAD pushed + green; **PyPI 3.0.74**; website **deployed** to production
(drydock.pages.dev + www.drydock-cli.com). Test suite **389 passing**.

**RMF/STIG program — COMPLETE end to end (raw benchmark → completed checklist):**
- **`/stig new <xccdf>`** (v3.0.70) — parses a raw DISA STIG **XCCDF benchmark**
  into a blank `.ckl` (the missing first arrow). Validated against the full
  **286-rule Application STIG** (U_ASD_STIG_V6R1) — 1.1MB valid `.ckl` in ~0.02s.
- **`/stig graph` CCI auto-mapping** (v3.0.74) — `drydock/cci.py` builds a
  CCI→800-53 map from DISA's `U_CCI_List.xml` (3551 CCIs, cached, offline-safe);
  `ingest_checklist` auto-creates `Control —SATISFIED_BY→ STIG-Rule` edges. Was a
  documented follow-on; now shipped. Closes RMF/STIG task #21's last gap.
- STIG ontology nodes + `/stig-remediate` (v3.0.69), assessor + engine (3.0.65-66).
- **`/stig` summary** (v3.0.73) — exact `/loop N` suggestion, scale hint for big
  checklists, no silent 50-cap truncation. Logic in `stig.summary_lines()` (tested).

**User-reported fixes (operator was at work):**
- **`/context [n]`** (v3.0.72) — view/set + PERSIST the context-window budget.
  Fixes the "stuck at 32768" trap: a stale `context_limit` in config.toml (drydock
  never rewrites an existing config) or a smaller server `-c`. NO 32k hardcode —
  default is 65536. See [[feedback_context_limit_32k_trap]].
- **GraphRAG ingests `.ckl`/`.cklb`** (v3.0.72) — checklists were silently
  skipped; now flattened to per-rule findings (extract.py). Verified end-to-end:
  the agent calls `Knowledge` and answers "which findings are open" from a `.ckl`.

**Real bugs found via hands-on TUI testing (the discipline pays off):**
- **Teardown crash** (v3.0.72) — the 0.18s `_tick_work` timer could fire during
  shutdown and `query_one("#status")` after widgets were gone → NoMatches/
  ScreenStackError crashed the app. `_refresh_status` now guards both. This was
  the root of the long-standing flaky TUI test (0/20 flake runs after the fix).
- **CCI fetch mkdir bug** — `cci.load_map` wrote to `.drydock/rmf/` before the dir
  existed → silent offline fallback. Mocked unit tests missed it; the live TUI run
  caught it. Fixed + regression test.

**Also:** broadened deterministic test coverage (web/mcp/rmf_graph/stig/cci edge
cases); repo hygiene (`.gitignore` .wrangler/.drydock/checklists); README +
website fully updated for the STIG suite + `/context`. Website deploy how-to:
[[reference_website_deploy]].

---
## ⭐ 2026-06-28 (cont.) — doc ingestion UX + skill authoring + self-documenting prompt

- **v3.0.60** — GraphRAG doc ingestion UX: `/graphrag add <path>` (incremental),
  `/graphrag query <q>` (test retrieval), `/graphrag status` lists sources.
  `graphrag.add_to_index()`/`sources()`.
- **v3.0.61** — `/skills new <name> <prompt>` authors a skill from the TUI
  (`skills.create_skill`, $ARGS supported); the **system prompt now documents
  Drydock's own slash commands** (`tuning._DRYDOCK_COMMANDS_HELP`) so the MODEL
  answers "how do I add docs / make a skill?" itself. Slash commands documented
  on README (table + sections), website, PyPI. All verified hands-on in the TUI.

## ⭐ 2026-06-28 — Docs + website updated for the new capabilities

- **v3.0.57** — README + `docs/PRD.md` rewritten for the agentic harness
  (Capabilities section, full tool/slash-command lists, dense-31B model, Python
  3.11+, corrected the stale "PyPI blocked" language). Published so the PyPI
  long-description updates too.
- **Website is now in the CLEAN repo** at `web/index.html` (fresh, clean-room —
  NOT from the radioactive old fork's `web/`). Live at **drydock.pages.dev** +
  **www.drydock-cli.com** (Cloudflare Pages project `drydock`, direct-upload).
  **Redeploy:** `web/` is the source; the box's `/usr/bin/node` is v22 (nvm's is
  v18, too old for wrangler), so:
  ```
  export CLOUDFLARE_API_TOKEN=$(cat ~/.config/drydock/cloudflare_token)
  export CLOUDFLARE_ACCOUNT_ID=$(cat ~/.config/drydock/cloudflare_account_id)
  PATH=/usr/bin:$PATH /usr/bin/npx --yes wrangler@latest pages deploy web \
    --project-name drydock --branch main --commit-dirty=true
  ```

## ⭐ 2026-06-27 — SHIPPED: agentic-harness feature push (3.0.50–3.0.56)

Built out the PRD "Drydock Agentic CLI Orchestration" capabilities. All on
GitHub + PyPI, each verified hands-on in the real TUI. **Tool registry now:**
Read · Write · Edit · Bash · Glob · Grep · todo · task · **Dispatch** ·
**GitStatus/GitDiff/GitLog/GitCommit** · **WebSearch/WebFetch** · **Knowledge** ·
**mcp__\<server\>__\<tool\>** (dynamic).

- **3.0.50 internet search** — `WebSearch`/`WebFetch` (`drydock/web.py`, stdlib
  DuckDuckGo POST + page-to-text; offline-safe). Model chained search→fetch live.
- **3.0.51 Version Control tools** — `GitStatus/GitDiff/GitLog/GitCommit`
  (`drydock/gittools.py`); structured + truncated; commit is local/reversible,
  push stays gated. Model chained status→diff→commit.
- **3.0.52 Skills** — `drydock/skills.py`; markdown skills in
  ~/.drydock/skills + <proj>/.drydock/skills, invoked `/<name>` ($ARGS subst);
  `/skills` lists them.
- **3.0.53 /loop** — `/loop <count> <prompt>` repeats a prompt (Esc stops).
  NOTE: loop state is `self._repeat` (NOT `self._loop` — collides with Textual
  App._loop).
- **3.0.54 multi-agent `Dispatch`** — runs up to 6 read-only sub-agents in
  parallel (shared `_run_subagent`; isolated _abort each). Serializes on the
  single-slot local server; parallel on multi-slot/backends.
- **3.0.55 MCP** — `drydock/mcp.py` clean-room JSON-RPC stdio client; config
  ~/.drydock/mcp.json ("mcpServers"); tools registered as mcp__server__tool at
  startup (crash-proof); `/mcp` lists them. Mock server in tests/fixtures.
- **3.0.56 semantic chunking** — `Read` on a >1500-line file with no window
  returns a STRUCTURE INDEX (def/class/header anchors) instead of dumping it.

PRD gap-analysis verdict: File System / Execution / Agent State were already
complete; the from-scratch items were **Version Control tools** + **semantic
chunking** — both now done.

---
## ⭐ 2026-06-26 (PM) — SHIPPED: PyPI live, TUI-driven launcher, GraphRAG, context fix

**Newest first (all pushed + on PyPI 3.0.49):**
- **v3.0.49 GraphRAG knowledge base** — users build a local entity-graph index
  from their docs/code; the agent retrieves from it via the read-only
  `Knowledge` tool. `drydock/graphrag.py` (clean-room, stdlib only — no
  embeddings/deps): chunk → entity extraction → co-occurrence graph → query with
  1-hop expansion. `/graphrag build <path>|status|clear` (TUI + CLI). Verified
  hands-on: model called Knowledge and answered KB-only facts. Tests:
  tests/test_graphrag.py.
- **v3.0.48 configurable `context_limit`** — was HARDCODED 65536 in cli.py (even
  clobbered `**cfg`), so a 32k-server user OOM'd before compaction fired and
  /compact found nothing. Now a config.toml setting (DEFAULTS < file <
  `--context-limit`); set it to your server's -c. Manual /compact also escalates
  to emergency_compact when a normal pass leaves history >50% full.
- **v3.0.47 thinking-visibility** (operator's feature, finished + rendered):
  `extract_thinking` + `ReasoningChunk` → collapsed `ReasoningCard` in the TUI.
- **v3.0.47 runaway text-repetition guard** (see below).



**Done this session (all pushed; HEAD past b6cedd7):**
- **`/compact` implemented FRESH** (TUI + CLI) — was advertised in `/help`, never
  wired. v3.0.46. Honesty fix: the ctx gauge only moves on a real shrink (found
  via hands-on tmux). Regression tests in `tests/test_compact_command.py`.
- **PyPI publishing is LIVE again** — `drydock-cli` 3.0.32 → **3.0.47 published**.
  NOT banned; active account token at `~/.config/drydock/pypi_token` (old
  quarantined account is the `.bak`). See Credentials section for the exact
  publish command. Earlier "blocked on reinstatement" notes were WRONG.
- **v3.0.47 — runaway text-repetition guard** (RESUME Task #41, half of it):
  `loop_detect.runaway_repetition_len()` + a throttled check in
  `providers.stream()`. When the model collapses into repeating one short unit
  (the `295:`×1365 failure), it trims the repeated tail, shows
  "[stopped — output began repeating]", and stops reading. Advisory, never
  raises. Conservative thresholds so legit repetition never trips it; 11 tests
  + hands-on TUI verified normal streaming is unaffected. (The other half —
  server-side `--repeat-penalty` — remains the operator's call.)
- **GitHub re-synced + auto-auth.** Local was 26 commits ahead (no cached creds →
  silent push failures). Pushed all; added a git credential helper reading
  `~/.config/drydock/github_token`, so `git push` now just works.
- **🎯 PRIMARY GOAL MET — TUI-driven tbench launcher.** `/data3/tbench_local/
  tui_task_lib.sh`: `source` it, then `ddt_up <task>` (builds the task's docker
  image, installs `drydock-cli` from PyPI inside, points it at gemma4 via
  host.docker.internal) → `ddt_tui <task>` (launches the REAL TUI in a tmux
  session `ddt_<task>` via `docker exec -it`) → drive by hand with
  `tmux send-keys`/`capture-pane` → `ddt_verify <task>` (runs the task's OWN
  `tests/test.sh`, prints reward 1/0) → `ddt_down <task>`. No `-p`, no judge
  pipeline — env-setup + the real verifier, hand-driven. This is THE path.

**Hands-on TUI baseline (3 tbench-2 tasks through the real TUI):**
- `fix-git` → **reward 1** (found lost commit via reflog, merged, resolved
  conflict). `nginx-request-logging` → **reward 1** (8/8 tests; the **sudo
  approval modal fired and `a`=Always worked** — a TUI-only path `-p` never hits).
  `sqlite-db-truncate` → not completed (model-slowness, see below), but used to
  stress-probe the TUI.
- **No drydock bugs found.** Validated: clean tool cards + Plan panel, no
  tool-call-as-text leaks, **binary tool output (`print(open(...,'rb').read())`)
  does NOT corrupt the transcript**, **Esc cleanly interrupts a long non-stream
  turn** and the session stays responsive after, empty-response path nudges once
  then breaks (no spin — code + observed), context gauge accurate, `/compact`
  works. The from-scratch TUI is solid.
- **Only real limiter = model speed**, exactly as the prior caveat said: dense
  31B at ~15 tok/s with `--reasoning-budget 20000` ⇒ a single hard-task turn can
  run 11+ min (sqlite recovery hit this). NOT a loop, NOT a drydock bug — the
  server even cancels at its default 600s timeout. Operator lever unchanged:
  lower `--reasoning-budget` in `/data3/Models/start_gemma4_31b_llamacpp.sh`.
  Note: during non-streaming tool turns the working-line token count holds at the
  session total by design (elapsed timer still advances to show liveness).

---
## ⭐ 2026-06-26 — LATEST STATE (READ THIS FIRST)

**Direction locked by the operator (2026-06-26):**
- **v3 (`/data3/drydock-v3`) is THE codebase.** The old `/data3/drydock`
  mistral-vibe fork is DEAD and RADIOACTIVE — do **NOT** read it, run it, or
  copy/port anything out of it. It is the inherited lineage/phone-home code that
  got the PyPI account banned. v3 stays **from-scratch / clean-room**. Missing
  features get **built fresh in v3**, never ported.
- **Improve drydock by USING its real TUI, hands-on (tmux).** `-p`,
  programmatic, pexpect, and stress harnesses do **NOT** count (rules #1 & #4
  below). Operator, verbatim: *"it has to go through the TUI. That is the whole
  point."*

**tbench was STOPPED (2026-06-26) — and why it matters:** the entire tbench
setup drove drydock via `drydock -p '<task>'` (one-shot). That exercises the
model + `agent.py` core but **bypasses the TUI entirely** — so the scores never
reflected the product the operator actually uses. Accordingly:
- Both harbor runs killed (`drydock_PASS3_v3.0.45_mult6_3box` + a
  `RERUN_FAILS` run). Relaunch crons PAUSED:
  `/data3/drydock/.pause_harbor_watchdog`, `.pause_tbench_chain`,
  `.pause_tbench_watchdog`.
- ~58 GB docker reclaimed (unused images + build cache); the `gemma4` server
  container/image preserved. Job result dirs (~1.3 GB) left at
  `/data3/tbench_local/jobs`.
- Last `-p` numbers (informational only, NOT a TUI measurement): pass@1 = 21/89
  (23.6%); the stopped pass@3 was ~25/89 (28.1%, partial).

**🎯 PRIMARY GOAL — TUI-driven testing (open task):** build a way to run tbench
TASKS through v3's **real TUI** (not `-p`): set up the task environment, launch
v3's TUI in tmux, feed the task prompt, let drydock work through the genuine TUI
code path, then run the task's existing verifier — verify by watching
`capture-pane`. Honor "no custom eval harness": reuse the real task defs +
verifiers and drive the genuine TUI; do not build a judge/batch-runner pipeline.

**Open task — build `/compact` FRESH in v3:** `drydock/cli.py` advertises
`/compact` in `/help` (line ~158) but `handle_command` never implements it.
Wire it to the existing `compaction.py` (`maybe_compact`/`emergency_compact`) +
the TUI, and verify hands-on in tmux. Pure from-scratch v3 work.

**⚠️ Heads-up (do NOT act on without operator OK):** the dead fork still has an
`auto_release` cron that could republish the banned-lineage package to PyPI.
Worth confirming it's disarmed — but check only the cron/flag, never the old code.

**Mistake not to repeat:** on 2026-06-25, before this decision, two fixes (a
read-timeout fix + a thinking-visibility feature) were committed into the OLD
fork `/data3/drydock` — the wrong tree. They are NOT in v3 and must NOT be
ported as code. v3 already shipped its own bash/read-timeout handling (v3.0.45,
`df51a06`). If those behaviors are wanted in v3's TUI, build them fresh.

---

## ⭐ 2026-06-24 OVERNIGHT — (history; superseded by the 2026-06-26 section above)

**Repo HEAD:** `93fbb46` (v3.0.44 + PRD update), CI **green**, all pushed. Repo PRIVATE.

**Fleet (all 4 boxes on dense 31B + vision, 64K ctx, persistent):**
- remus/Dell `.22:8000` (docker `llamacpp-gemma4-31b`, this box), romulus
  `.21:8000` (systemd), 3090 `.129:8000` (Windows `fbobe@`, watchdog
  scheduled-task **every 24h**), Jetson `.19:8080` (systemd, `/opt/models` NVMe).
- **Jetson caution:** under the 31B it *thrashes* (5 tok/s) until SSH +
  llama-server both time out, though the host stays up (17d uptime). It
  recovers when idle. Excluded from scoring runs (it converts passable tasks
  to false-timeouts). The harbor adapter now health-probes + skips dead boxes.
- Rollback to 26B-A4B: `/data3/Models/gemma4_restore_config.txt`.

**Shipped 2026-06-24 (v3.0.39→44, all CI-green + pushed):**
- v3.0.39 concrete default `base_url`; v3.0.40 Python floor 3.12→3.11
  (v2-upgrade compat); **v3.0.41 active v2→v3 config migration** (back up
  legacy/foreign config.toml, write fresh editable one — verified on the real
  v2 config on the Windows 3090).
- **v3.0.42** `-p`/one-shot surfaces unreachable LLM as the actionable message
  + exit 2 (was a raw traceback); purged `qwen` from test fixtures.
- **v3.0.43** plan panel cleared at each user-turn start (stale-plan bug, found
  by hands-on TUI use, verified live).
- **v3.0.44** `-p` trace logs tool inputs + outcomes (timeout diagnosability).
- harbor_fork adapter: **skips unreachable backends** in the rr pool; wheel
  launch helper `/data3/tbench_local/tbench_launch_lib.sh` (never-404).
- Fixed the operator's live `~/.drydock/config.toml` (qwen→gemma4 + base_url);
  de-flaked a CI-flaky TUI test; refreshed editable install → `--version` 3.0.44.

**tbench harness** (`/data3/harbor_fork`, adapter `.../installed/drydock_agent.py`).
Container networking was fixed earlier (ufw `DEFAULT_FORWARD_POLICY=ACCEPT` +
docker restart + iptables `INPUT -s 172.16.0.0/12 --dport 8899 ACCEPT`). Wheel
served by host `python -m http.server 8899` over `/data3/drydock-v3/dist/`.
- **Use the launch helper:** `source /data3/tbench_local/tbench_launch_lib.sh;
  tb_prepare_wheel 3.0.44` (builds-if-missing, verifies HTTP 200, sets
  `DRYDOCK_INSTALL_SPEC`). Pool env `DRYDOCK_BACKEND_POOL=<comma list>`; rr
  counter `/tmp/dd_backend_rr.ctr`. Eval: `result.json` →
  `stats.evals[ev].reward_stats.reward` (1.0=pass); `is_resolved` is unreliable.

**Results (dense 31B, full 89-task terminal-bench-2 corpus):**
- **pass@1 = 21/89 ≈ 23.6%** (clean 3-box, Jetson excluded). V2PASSED10 = 8/10.
- **pass@3 IN PROGRESS** (extended `--agent-timeout-multiplier 6`): job
  `drydock_PASS3_v3.0.44_mult6_3box`, 68 not-yet-passing tasks ×3 (21 pass@1
  winners carried forward in `/tmp/pass3_winners.json`), 3-box pool. Monitor
  `/tmp/pass3_progress.log`. Was ~29% climbing when the operator went to bed.
  Merge = winners ∪ retry-rescued. Self-monitoring; report the headline + which
  tasks the extended timeout rescued when it finishes (~20-40h run).

**Doom-loop verdict vs 26B-A4B (data, 219-239 trials + hands-on TUI):** the
dense 31B does NOT reproduce the 26B's hard tool-loops. Byte-identical guard
(`agent.py IDENTICAL_REPEAT_CAP=8`) fired 36/239 trials, caps at 8; ~85% never
trip it; 0 loops in ~8 hands-on TUI tasks. **One rare different failure:**
text-repetition collapse (make-mips-interpreter emitted `295:` ×1365), 1 task /
219, not seen by the tool guard. **Task #41 (queued, deferred until fleet is
free):** server-side `--repeat-penalty 1.1` (+ optional `--dry-multiplier 0.8`)
in `/data3/Models/start_gemma4_31b_llamacpp.sh`, and/or a drydock-side text-loop
guard. Server currently runs with NO repetition control.

**Open caveat:** 31B is slow on hard frontier tasks (compiler builds, ray
tracers, ARC-AGI) → genuine timeouts even at mult 6 (NOT loops — diagnosed).
Lever = lower server `--reasoning-budget` (currently 20000), operator's call.
---

## Current state (as of HEAD 956e017)

- **177 tests pass, ruff + pyright clean, release wheel builds 0 HIGH findings.**
- Local == `origin/master`, nothing uncommitted/unpushed.
- Feature-complete vs the original task list + the major v2 reliability
  hardening is ported. Backlog #1–#27 all done.
- 13 shakedowns passed (6 medium + 5 hard + 2 failure-recovery), zero
  text-form tool-call leaks.

### What's implemented
- TUI (Textual): transcript, streamed text, collapsible tool cards, multi-line
  prompt (`Ctrl+J` newline, `Enter` submits), `↑/↓` history (persists to
  `~/.drydock/history`), `Ctrl+O` expand tools.
- Slash commands: `/model /cwd /undo /back /status /clear /help /quit`.
- Config: `~/.drydock/config.toml` (defaults < file < CLI flags; NEVER modifies
  an existing file — only creates on first run). First-launch local-LLM
  autodetect (probes :8000/:11434/:1234).
- Safety: catastrophic-command denylist (`rm -rf /`, `mkfs`, fork bombs — hard
  refused) + approval modal for sensitive commands (`sudo`, installs, network,
  `git push` → Allow/Always/Deny).
- Guards (advisory, never block): syntax, main-entry, stub-only, missing
  sibling imports, bare-raise; conflict-marker Write/Edit refusal.
- Gemma reliability: non-streaming on tool turns, thinking/special-token
  stripping, hallucinated-tool redirect, text-form tool-call recovery + retry,
  adaptive `reasoning_effort` (high-to-plan / low-to-continue), loop nudges +
  same-path write-thrash advisory, blank/dir-path guard, two-tier compaction.

### Module map (`drydock/`)
`agent.py` (loop, adaptive reasoning, hallucinated-tool redirect, drop_last_turn) ·
`providers.py` (OpenAI-compatible, non-streaming for Gemma tools, unreachable
error) · `bash_safety.py` (denylist + approval tier) · `guards.py` (advisory
write guards + conflict markers) · `loop_detect.py` · `tuning.py` (Gemma tuning) ·
`compaction.py` · `config.py` · `detect.py` · `tools/__init__.py` (Read/Write/
Edit/Bash/Glob/Grep + undo journal) · `tui/{app,widgets,approval,messages}.py`.

## How to run / test / verify

On the **workstation** (this is where it runs — Python at
`/home/bobef/miniconda3/bin/python3`, has textual/openai):

```bash
cd /data3/drydock-v3
# Run the TUI (in a project dir; model on local :8000)
PYTHONPATH=/data3/drydock-v3 /home/bobef/miniconda3/bin/python3 -m drydock --model gemma4 --provider vllm
# Tests — the local pytest_cov plugin is broken, so disable it:
/home/bobef/miniconda3/bin/python3 -m pytest tests/ -q -o addopts= -p no:cov -p no:cacheprovider   # 177 pass
/home/bobef/miniconda3/bin/python3 -m ruff check drydock/ tests/
/home/bobef/miniconda3/bin/python3 -m pyright --pythonpath /home/bobef/miniconda3/bin/python3 drydock/
DRYDOCK_PY=/home/bobef/miniconda3/bin/python3 ./scripts/release.sh   # build + security scan
```

(Fresh clone elsewhere instead: `python3 -m venv .venv && . .venv/bin/activate
&& pip install -e ".[dev]"`, then `drydock` / `pytest tests/ -q` / `ruff` /
`pyright` are on PATH and need none of the workstation flags.)

## NON-NEGOTIABLE working rules (learned the hard way)

1. **Verify harness-side fixes in a REAL tmux TUI session** — not just pytest.
   `tmux new-session -d -s v3 -x 200 -y 50 'cd <dir> && PYTHONPATH=/data3/drydock-v3 /home/bobef/miniconda3/bin/python3 -m drydock --model gemma4 --provider vllm'`,
   then `tmux send-keys` a real prompt, `tmux capture-pane -p` to read it, and
   run the built code functionally. `--help` is NOT a test.
2. **Isolate `HOME` for TUI tests:** prepend `HOME=/tmp/v3_home` (mkdir it).
   This box also runs the operator's v2 install, and both share
   `~/.drydock/{config.toml,history}` — a dead-port test once leaked `base_url`
   into the operator's real config. Always sandbox HOME for test runs.
3. **Never let ruff/lint autofix delete side-effect imports.** `ruff --fix` once
   removed `import drydock.tools` from agent.py (it registers the tools), leaving
   an empty registry → the model emitted tool calls as TEXT and nothing ran.
   agent.py now calls `register_all()` explicitly with a regression test.
4. **NO custom eval harnesses.** Drive the TUI by hand, one prompt at a time.
5. **Advisory, never blocking** — loop/guard mechanisms inject context; only
   hard stops are the catastrophic denylist and max-tool-turns.
6. **Loop-breakers return a result string, never raise** (raising spawns its own
   loop on long tasks).
7. `scripts/security_scan.py` gates every release (exit 2 = HIGH = block).

## Credentials / env (workstation)
- GitHub: token at `~/.config/drydock/github_token` (user `fbobe321`). A global
  git credential helper now reads that file, so plain `git push` auto-auths and
  picks up any token rotation — no inline `GH_TOKEN` dance needed. (Set up
  2026-06-26.) `gh` at `/home/bobef/miniconda3/bin/gh`.
- Dev/test Python: `/home/bobef/miniconda3/bin/python3` (3.12, has textual/openai).
- tbench notifications paused via `/data3/drydock/.pause_tbench_*` flags.
- **PyPI: PUBLISHING IS LIVE (corrected 2026-06-26).** `drydock-cli` is on an
  ACTIVE account — token at `~/.config/drydock/pypi_token` (the OLD quarantined
  account's token is `~/.config/drydock/pypi_token.quarantined_account.bak`,
  unused). v3.0.46 was published this way. The earlier "reinstatement pending /
  publishing blocked" notes were WRONG. Publish: build, run the provenance scan,
  then `SETUPTOOLS_USE_DISTUTILS=stdlib python -m twine upload -u __token__ -p
  "$(cat ~/.config/drydock/pypi_token)" dist/*` (the env shim dodges a
  jaraco.functools circular import in this box's conda twine). `scripts/release.sh`
  documents the same.

## Suggested next steps (pick up here — 2026-06-26)
1. **Hands-on v3 TUI shakedown** — drive v3's real TUI in tmux (per rule #1),
   establish a clean baseline of what works / what's missing in the from-scratch
   build, with `capture-pane` evidence. This is how we find what to build next.
2. **Build `/compact` fresh in v3** (advertised in `/help`, not implemented —
   wire to `compaction.py`). Verify in tmux.
3. **Build the TUI-driven test path** (PRIMARY GOAL above): run tbench tasks
   through v3's real TUI instead of `-p`, then use it to find + fix real bugs.
4. Do NOT touch / read / port from the old `/data3/drydock` fork.
5. PyPI publishing WORKS now (see Credentials) — `drydock-cli` was at 3.0.32,
   v3.0.46 published 2026-06-26. Bump version → build → provenance scan → twine
   upload with the active token. (Docker republish still per operator.)

Earlier backlog (v3 hardening port, retrieval, etc.) is essentially done — see
the "What's implemented" + history sections above.

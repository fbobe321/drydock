# Self-Distillation Week Run — operator guide & running log

**Started:** 2026-07-27 08:55 (workstation, unattended, week-long trip).
**Owner script dir:** `/data3/tbench_local/frontier/selfdistill/`
**One-command status:** `bash /data3/tbench_local/frontier/selfdistill/selfdistill_digest.sh`

---

## What it does (closed-loop, gated self-distillation)

An autonomous loop over generations. Each generation:

1. **COLLECT** — 2 streams drive the **real drydock TUI** (docker, one container per task)
   over the tbench-2 pool (73 tasks = 89 minus 10 base-solved minus 6 held-out), split
   `pool_s0.txt`/`pool_s1.txt`. Per task: base-fail **gate** → best-of-8 **research assist**
   → **harvest** the verified `base✗ → assist✓` trajectory. Window = 8h (`COLLECT_WINDOW`).
2. **TRAIN** — when the trainable corpus has grown by ≥2 traces since the last train
   (`MIN_NEW_TRACES`, accumulates across generations) and totals ≥3 (`MIN_TOTAL`):
   stop the server (free GPU) → canary-scrub raw trajectories → `compass harvest-tbench`
   → **QLoRA** (`compass train`, 150 steps) → convert to `selfdistill-gen<N>-lora.gguf`.
3. **EVAL** — serve base+LoRA, run the **frozen held-out set** (6 tasks, never trained) and a
   4-task **regression sample**, plain, through the **real TUI + each task's own verifier**.
   `heldout_solves` = transfer signal; regression must not break.
4. **PROMOTE (gated)** — if the LoRA solves ≥1 held-out task **and breaks no easy task** and
   beats the best prior score, it becomes the **champion** that collects the next generation
   (the genuine RSI step). Otherwise the champion is unchanged. **Base is always the fallback**,
   so the worst case is a week of base-model collection — strictly ≥ the prior setup.

GPU is single (2× RTX 4060 Ti, tensor-split). Server (collect/eval) and training are
**mutually exclusive**; the one sequential loop guarantees that. **Not an eval harness** — every
task runs through the real TUI + real verifier (same lineage as the authorized collector). No `-p`.

## Why (context)

Standing verdict: self-distill is **DATA-constrained** — the `base✗→assist✓` AMBER band is thin
(2 traces in ~2 weeks of prior grinding). This run maximizes the band (full 89-pool, best-of-8,
a full week) and closes the loop honestly. If no transfer ever appears, that is itself the
finding — the loop degrades gracefully to measurement. See memory
`project_compass_selfdistill_verdict`.

## Files

| path | what |
|---|---|
| `selfdistill.sh` | the orchestrator (`loop`/`once`/`serve`/`stop`/`digest`) |
| `selfdistill_launch.sh` | flock single-instance launcher (holds `loop.lock` for the loop's life) |
| `selfdistill_watchdog.sh` | cron guardian — relaunches a dead loop |
| `selfdistill_digest.sh` | one-command status |
| `heldout.txt` / `regression.txt` / `pool_s*.txt` | frozen task lists |
| `corpus/raw/*.json` | unified trainable trajectories (held-out excluded) |
| `gens/gen<N>/` | per-generation artifacts (harvest, gate csv, sft, logs, eval) |
| `eval_results.csv` | per-generation transfer/regression + promotion decision |
| `state.env` | `GEN`, `BEST_SCORE`, `LAST_TRAINED_TOTAL` |
| `champion.txt` | `base` or `/data3/Models/selfdistill-gen<N>-lora.gguf` |
| `selfdistill.log` / `heartbeat.txt` / `watchdog.log` | logs |

## Survival (survives SSH drop, crash, reboot)

- The loop runs `setsid nohup` (detached). Collectors run in server-side tmux (survive SSH drop).
- Model server: docker `--restart unless-stopped`.
- **Cron** (`crontab -l | grep selfdistill`):
  - `@reboot` → relaunch the loop 60s after boot.
  - `*/15 * * * *` → watchdog: if `loop.lock` is free (loop dead), relaunch it.
- Single-instance: the launcher's `flock -n` means a double-launch simply exits.
- `collect()` purges orphaned collectors/containers at the start of every generation, so a
  watchdog relaunch can never over-subscribe the 2-slot server.

## Operate it

```bash
D=/data3/tbench_local/frontier/selfdistill
bash $D/selfdistill_digest.sh                 # STATUS (start here)
tail -f $D/selfdistill.log                    # live loop log
cat $D/eval_results.csv                        # generation results
tail -f $D/gens/gen0/collect_s0.log            # what a stream is doing right now

# stop the whole run (for the trip you do NOT need this):
crontab -l | grep -v selfdistill | crontab -   # disarm cron first
setsid bash -c 'for p in $(pgrep -f "selfdistill_launch|selfdistill.sh loop|frontier_collect_n3"); do kill $p; done'
rm -f $D/loop.lock
docker rm -f $(docker ps -aq --filter name=ddt_) 2>/dev/null   # task containers only
```

Tunables are env vars at the top of `selfdistill.sh` (`COLLECT_WINDOW`, `MIN_NEW_TRACES`,
`MIN_TOTAL`, `BEST_OF_N`, `CUTOFF`, `TRAIN_STEPS`, `TRAIN_SEQ`, `EVAL_N`).

---

## Running log (newest first)

- **2026-08-06 — 🧬 EVOLUTIONARY UPGRADE (all 6 framework gaps) + 2-stream POOL SWEEP running.** Applied the
  Information→Replication→Variation→Selection lens: the ratchet was a degenerate (1+1) hill-climber; self-distill's
  train-on-packed-corpus is a lethal variation operator (composition wall). Built: **QD `Archive`** (elite per
  behaviour niche), **`plan_crossover`** (splice complementary partial solutions — the composition-wall cure in
  environment-space), **`VariationPolicy`** (stall escalation exploit→diversify→fanout→crossover→restart),
  **`Candidate` lineage**, **`pareto_front`** (multi-objective: checks/generalization/−cost) — in
  `drydock/ratchet.py`, 42 unit tests. **`speciate.py`** (rec 5): cluster traces → per-cluster adapters.
  **`evolve_bridge.py`** (tested offline: replays the 5/6→6/6 stall-escalate + a crossover plan) drives
  **`ratchet_evolve.sh`** (docker/TUI glue — LIVE-UNVALIDATED, one GPU smoke run pending post-sweep).
  Meanwhile the **POOL SWEEP** (`sd_train_oneshot_ratchet_pool2.sh`, 2 parallel streams over 68 ranked
  candidates, early-abort on no-gradient, resumable) measures the base ratchet's reach — tally in
  `ratchet/ratchet_pool_results.csv`, Telegram + done-file notifier armed. Early: openssl-selfsigned-cert 6/6,
  bn-fit-modify 9/9, hf-model-inference 4/4.

- **2026-08-05 — ⚙️ RATCHET (Dawkins cumulative selection) built + under test; normal loop STOPPED (operator).**
  The stuck-at-7 volume ceiling is a COLLECTION limit: best-of-N gives the base a fresh container per attempt
  (full backslip every try), so any task needing more than one attempt's work is uncrackable at any N.
  **`ratchet_solve.sh`** adds a pawl — fitness = the real checker's pass-count (CTRF `passed/total`, graded);
  on improvement `docker commit` locks in progress, on no-gain it rolls back to the best snapshot next round;
  a continuation prompt tells the model its work is preserved + the current pass-count. Anti-cheat: the checker
  is stripped (`rm -rf /tests /logs`) BEFORE the snapshot, so persisted state never contains the tests. Same
  base model, no stronger teacher.
  - **First sweep** (`sd_train_oneshot_ratchet_run.sh`: kv-store-grpc, nginx-request-logging): both SOLVED at
    round 1 (7/7, 8/8) → positive but INCONCLUSIVE — a round-1 solve never exercises the across-rounds pawl.
  - **Operator: "stop the normal run, progress further with the ratchet."** Loop stopped by specific PID (no
    broad `drydock` pkill); base HF-Q5 server kept up. Running **`sd_train_oneshot_ratchet_hard.sh`** (NO-relaunch
    variant, named `sd_train_oneshot*` so the watchdog defers) on 3 hard, richly-graded, non-held-out/non-regression
    tasks — **db-wal-recovery, llm-inference-batching-scheduler, compile-compcert** (6 rounds, 1200s each).
    db-wal-recovery round 1 = **0/7** (first genuinely-hard round-1 miss → the across-rounds path now actually
    fires). Real signal = `best` climbing above 0 in `ratchet/ratchet_hard_results.csv`.
  - **Wrinkle:** `best` starts at `-1`, so a 0/N round-1 snapshots as an "improvement" (harmless; only best>0 is
    real progress). **Completion notice armed:** Telegram (`tg_notify_ratchet_hard.sh`, detached) + in-app push.
    Normal loop intentionally left DOWN (watchdog would resume it ~15min post-sweep unless the cron is paused).

- **2026-08-01 08:15 — 🎯 DECISION: GRIND VOLUME FOR WEEKS + TEST GENERALIZATION (operator).** Memorization
  path is dead (composition 0/21); committing to the long bet: accumulate a large diverse condensed corpus
  and watch whether GENERALIZATION emerges. Infra is ready and unattended: loop collects (best-of-8 +
  **P4 corpus-RAG** yield-boost) → condenses (P1+prune) → **trains when corpus grows +2 traces** (currently
  7; next train at 9) → evals frozen 6 held-out on the **matched HF-Q5 base** (honest baseline) → gated
  promote. Self-heals via `@reboot` + `*/15` watchdog. **Generalization signal = `heldout_solves>0` in
  eval_results.csv.** NEW: `selfdistill_signal.sh` (cron every 6h) Telegrams ONLY on meaningful events — a
  held-out hit, a promotion, a corpus milestone (15/25/40/60) — plus a daily still-alive heartbeat, so a
  multi-week run needs no watching. Reality: yield ~0–1 trace/window (gen12 folded 0), so this is a
  multi-week accrual; early eval rows will be null until volume is large. Watch: /data3/Models LoRA gguf +
  gens/ logs accumulate over weeks (4.2TB free — fine, but a cleanup could be added later).


- **2026-08-01 00:45 — overnight autonomous (operator asleep, "process all the todo"): P4 shipped +
  loop eval made valid; fork-1 rate-pinning running.**
  - **FORK 1 DONE — 🏁 composition wall DEFINITIVE: 0/21** (0/3 on every one of the 7 trained tasks, 3× each;
    `multirun_results.csv`). The earlier composition 2/7 was NOISE (break-filter 0/3 here). Multi-task
    condensed reproduction is ~0 regardless of rank(32/128) or pruning; single-task is robust (break-filter
    3/3 as its own LoRA). **The memorize-many-solutions path to a tbench-solving LoRA is DEAD; only remaining
    bet = GENERALIZATION via corpus VOLUME (P2).** Loop resumed collecting (gen13) after, data-starved.
  - **P4 DONE + ENABLED:** `corpus_rag.py` (stdlib TF-IDF retrieval) returns the most-similar PAST verified
    solve as an analogical hint for the research assist (RSI compounding). Wired into `frontier_collect_n3.sh`
    (`CORPUS_RAG`, graceful — errors→no injection) and turned ON in the loop (`selfdistill.sh` collect passes
    `CORPUS_RAG=1`). HARD safety: never retrieves the current task or any held-out task (verified). Sharpens
    as the corpus grows. The harvested trace is still keyed to the plain instruction → no training contamination.
  - **Loop eval MADE VALID (serve() TODO resolved):** `serve()` is now CONDITIONAL — QAT 65536/np2 for
    base-only COLLECT (proven, throughput), MATCHED HF-Q5 32768/np1 when a LoRA is applied (eval/champion;
    a HF-trained LoRA is inert on QAT). New `serve hfq5` = matched base PLAIN for the baseline, so
    `measure_baseline` now measures held-out + regression on HF-Q5 (no more `BASE_HELDOUT=0` assumption — the
    stronger HF-Q5 may solve some held-out tasks; the gate must credit the LoRA only for real transfer).
    baseline.env invalidated → re-measures. After fork 1 the loop resumes P2 collection with all of the above live.
  - Code lives in non-git `tbench_local` (corpus_rag.py, frontier_collect_n3.sh, selfdistill.sh) — on disk,
    NOT on GitHub (no remote there); RESUME (pushed) documents it. general_condense/PRD in compass git.


- **2026-07-31 19:20 — ⚠️ MULTI-TASK COMPOSITION WALL found; P1 shipped; composition diagnostic running.**
  - **P1 DONE + committed/pushed:** `general_condense.py` now keeps the artifact-producing RUN step (Bash
    after the last file edit; pure-Bash solves keep their command sequence). Unlocks run-based tasks
    (distribution-search now writes files + runs `python3 solve_kl_v8.py`; build-pmars keeps its build
    sequence). compass `3bbb19d` (local); drydock-v3 RESUME pushed `863e34f..940e511` (fbobe321/drydock main).
  - **Held-out generalization test (multi-task, 7 condensed traces, r=32) — 0/6 held-out AND 0/7 trained.**
    Held-out 0 is expected (data-limited). The SURPRISE: **0/7 reproduction** — including break-filter, which
    the SINGLE-task LoRA solved 3/3. Training fully memorized (**loss→3e-5**), so NOT undertraining. Packing 7
    `task→solution` mappings into one r=32 adapter memorizes each under teacher-forcing but **fails to
    RETRIEVE any at free-generation inference** → a multi-task interference / over-overfit wall. Single-task
    reproduction is robust; naive multi-task composition is not. This is the real obstacle to "one LoRA that
    completes tbench" → **P2 volume is premature until composition is cracked.** (`condensed_multi_results.csv`)
  - **COMPOSITION diagnostic — VERDICT 20:29: 2/7 at r=128** (up from 0/7 at r=32). `composition_results.csv`:
    break-filter ✓, custom-memory-heap-crash ✓; large-scale-text-editing, constraints-scheduling,
    bn-fit-modify, distribution-search, build-pmars ✗. **Rank IS a lever (0→2) but not the whole story.** The
    split is by SOLUTION COMPLEXITY: the 2 that reproduce are short single-file self-contained fixes; the 5
    that fail are multi-file / run-heavy / computational — and fail even with P1 run-steps + full memorization
    (free-generating a long exact numerical script or multi-step build is much harder than one short payload).
  - **(2) target-pruning DONE + RETEST: 0/7** (`composition2_results.csv`). `general_condense` now prunes
    dead file-versions (distribution-search 9→2 = solve_kl_v8+verify_kl; break-filter keeps out.html via
    keep-all). Retrained r=128 on the pruned+scrubbed 7 (loss→0.005, adapter+serve verified healthy) →
    **0/7** — including break-filter & custom-memory-heap-crash which were 1 unpruned. Since their pruned
    targets are IDENTICAL, `2/7`(unpruned) vs `0/7`(pruned) is **within-noise variance on a low base rate**,
    NOT "pruning hurt". **HONEST VERDICT: multi-task composition reproduces poorly (~0–2/7) regardless of
    rank (32→128) or target pruning — it is NOT cracked.** Methodology note: these were SINGLE-run evals;
    single-task tests used 3 runs (robust). Multi-task memorization of many solutions in one adapter is
    fundamentally brittle.
  - **IMPLICATION / next decision:** the "one LoRA that MEMORIZES many tbench solutions" path is blocked by
    the composition wall. The remaining bet is **GENERALIZATION via volume** (learn the skill, not memorize
    solutions) — untested, needs P2 volume collection (slow, data-starved). OR accept the formula as a
    single-task/small-batch tool. Options on the table: (i) multi-run eval to pin the true multi-task rate;
    (ii) commit to P2 volume + measure whether generalization emerges as corpus grows; (iii) per-task /
    per-cluster adapters (defeats a single general LoRA). Tracked #13.

- **2026-07-31 08:10 — 🚀 MISSION PHASE 2: SCALE THE FORMULA TO SOLVE TBENCH.** Recipe proven
  (raw✗→condensed✓); condensing wired into `train()`; LoRA+dataset published to HF. Operator: build traces
  in phases into a dataset + LoRA that completes tbench, hoping for generalization. **Formula:** verified
  `base✗→assist✓` TUI solve → CONDENSE (`task→winning file edits→verify`) → QLoRA on the MATCHED base →
  reproduces reliably. **Phases:** P1 extend `general_condense.py` to keep the artifact-producing run step
  (unlock run-based tasks — today only file-write solutions condense, ~½ the pool is blocked); P2
  volume-collect condensed traces over the full 73-pool in batches (7→30→50+); P3 retrain + eval frozen
  6-task held-out per batch (compounding curve), serve on MATCHED HF-Q5 base; P4 corpus-RAG assist to raise
  yield; P5 publish milestones. WIN = held-out tasks flip 0→1 as the corpus grows. In flight: held-out
  test at n=7 (`condensed_multi_results.csv`). Tracked drydock-v3 tasks #8–12.


- **2026-07-30 21:00 (evening session — "make it work")** — **gen8 = first HONEST result, then a
  root-cause hunt.** gen8 landed `ho=0/6(base=0) reg=1/4(base=2) promoted=no` — the loop works
  end-to-end and the gate correctly refused a non-transferring, mildly-regressed LoRA (champion stays
  base). But gen8's full 4h COLLECT folded **0 new traces** → the run is **collection-starved**: absent
  new data, every gen retrains the same 7 traces to the same null.
  - **Found a real train/serve BASE MISMATCH:** the loop TRAINS+converts LoRAs against
    `gemma-4-31B-it-hf` but SERVES them on `gemma-4-31B-it-qat-UD-Q4_K_XL.gguf` (a *different*
    checkpoint) → adapter delta applied from the wrong origin. Built a **matched** serve base
    `gemma-4-31B-it-hf-Q5_K_M.gguf` (20.8GB; built `llama-quantize` on the host, f16→Q5_K_M).
  - **TESTED it (3×3 reproduction matrix via the real TUI, `sd_train_oneshot_reprotest.sh` →
    `reprotest_results.csv`)** over 3 base-hard trained tasks × {HFbase+LoRA, HFbase-plain, QATbase+LoRA}.
    **Result: HFbase+LoRA ≡ HFbase-plain (0,1,0 both) → the gen8 adapter is INERT even on the matched
    base.** So the mismatch is a real latent bug but **NOT** why self-distill fails. The one solve
    (`bn-fit-modify`) is a base-CAPABILITY effect (HF-Q5 solves it plain; QAT can't — and "base✗" during
    collection was measured on QAT). Adapter load was verified in server logs (not a load failure).
    Suspected real blocker: **adapter too weak** (gen8 = 60 steps, loss 0.18, r=16 — the loop cut steps
    150→60 to AVOID memorization, which is exactly what the "if it can solve it we can train it"
    hypothesis needs).
  - **hard-overfit (raw) — VERDICT 22:43: INERT 0/3.** `sd_train_oneshot_hardmemo.sh` overfit the raw
    `distribution-search` trace to loss→0.0005 (r=32), served the matched HF base (adapter load verified),
    ran ×3 → `hardmemo_results.csv` = **0,0,0**. Even a fully-memorized adapter on the matched base does
    NOT reproduce the trained agentic task. ⇒ weight-space memorization ≠ agentic execution at inference.
    Confirms the PRD's "memorized ≠ re-executes" (07-22) — I'd trained the RAW trajectory, the format shown
    not to transfer. (Aside: `distribution-search` also needs a *run* step, which `general_condense` drops.)
  - **CONDENSED test — VERDICT 01:40: WIN, 3/3.** `sd_train_oneshot_condensed.sh` overfit the CONDENSED
    solve of `break-filter-js-from-html` (task→`Write /app/out.html`→verify), matched base → 3/3 solves
    (`condensed_results.csv`), each ~1.5 min (went straight to the memorized fix). Clean same-task A/B:
    raw+LoRA=0 AND plain=0 in the reprotest → **`raw✗ → condensed✓`**. **THE FIX IS PROVEN: the loop must
    CONDENSE traces (task→winning file edits→verify) before training, not train raw trajectories** — this
    explains 8 gens of null transfer. Operator hypothesis HOLDS in condensed form. Reproduces PRD 2026-07-22
    19:02. Caveat: `general_condense` drops Bash/run steps, so it fully captures FILE-WRITE solutions
    (break-filter) but not RUN-based ones (distribution-search, build-pmars) — those need the run step kept.
  - **NEXT (operator directed "a then c then publish"):** (a) wire condensing into the loop's TRAIN phase
    (`selfdistill.sh` train() → condense before harvest); (c) train a condensed MULTI-task LoRA on all 7
    traces + eval the 6 HELD-OUT tasks (generalization, the real RSI test) via `sd_train_oneshot_condensed_multi.sh`;
    then PUBLISH the condensed LoRA to HuggingFace `fbobe3/` with a full write-up. Tracked drydock-v3 tasks #5–7.
  - **New artifacts:** `gemma-4-31B-it-hf-F16.gguf`, `gemma-4-31B-it-hf-Q5_K_M.gguf`,
    `build/llama.cpp/build/bin/llama-quantize`, `sd_train_oneshot_reprotest.sh`,
    `sd_train_oneshot_hardmemo.sh`, `reprotest_results.csv`, `hardmemo_results.csv`,
    `gens/hardmemo/dist_sft.jsonl`. Both experiment scripts are named `sd_train_oneshot*` so the
    watchdog DEFERS relaunch while they hold the GPU, and they relaunch the loop on exit — the week-run
    keeps self-healing between borrows. Both GPU borrows restored the loop cleanly (verified single
    flock instance, gen9 COLLECT resumed).

- **2026-07-30 08:27** — **Fixed a critical PATH bug that had silently killed the TUI since the
  cron relaunch.** `tmux` lives in `/home/bobef/miniforge3/bin` (not in cron's minimal PATH), so
  after the watchdog relaunched the loop via cron, EVERY `tmux` call no-op'd → collection harvested
  0 and gen7's eval scored 0/6+0/4 **with no task actually run** (the "regression breaks" were a
  false alarm). **Fix:** the script now `export`s a full PATH (survives cron/@reboot), verified
  tmux/uv/docker resolve; collectors now genuinely drive the TUI (pane shows `Bash`/`⚓ working`).
  Also: **base baseline** added — regression is now judged vs what base actually does at 20-min-plain
  (`baseline.env`), and promotion requires REAL transfer (held-out > base) with no regression vs base.
  **TRAIN_STEPS 150→60** (gen7 memorized to loss 0.004). Reset train trigger + dropped the bogus
  gen7 row so gen8 produces the first HONEST train→baseline→eval result on the 7 traces.
  Corpus 7 (frozen ~1 day by the PATH bug; now collecting again).


- **2026-07-29 20:40** — **Fixed a critical TRAIN bug.** Collection was healthy (corpus 2→7 in
  2 days, 7 gens self-healed), but **every TRAIN OOM'd (14×)** → `eval_results.csv` empty, loop
  couldn't close. Root cause: (1) `stop_server` didn't wait for llama.cpp to release VRAM, so
  `device_map="auto"` saw GPU1 full and crammed the whole 4-bit model onto GPU0; (2) `seq=1024`
  (precedents used 512). **Fixes:** `stop_server` now polls until both GPUs are freed (<1.5GB)
  before training; `TRAIN_SEQ` default → 512; `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.
  **Confirmed working** — training now runs with the model split across both cards (13.9+12.0GB),
  step 1/150 loss 3.21, no OOM. Also: collect window 8h→4h (faster train cadence), added
  `EVAL_CUTOFF=1200` (bounds per-gen eval cost), and a **train-aware watchdog** (won't relaunch the
  loop into an out-of-loop training run). Ran `sd_train_oneshot.sh` to validate end-to-end incl.
  GGUF; loop auto-resumes via watchdog after. Crons re-armed.


- **2026-07-27 08:55** — Run LIVE. gen0 COLLECT started, champion=base. Corpus seeded with 2
  prior verified traces (`custom-memory-heap-crash`, `large-scale-text-editing`). Two streams
  gating base-plain on `bn-fit-modify` (s0) / `break-filter-js-from-html` (s1) through the real
  TUI. Crons armed. Built + debugged this session: fixed collector path, canary-scrub in train,
  cross-generation train trigger, orphan-purge on collect start, single-line logging.

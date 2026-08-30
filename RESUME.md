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

> **🧬 2026-08-24 — THE META-RATCHET: ratchet the FITNESS SIGNAL itself (a good check may matter as
> much as solving). Measurement infra live on a freed .20 lane.**
> Operator: the self-authored fitness signal "may be as important as solving a task — describing a
> problem well is prerequisite to solving it — almost need a ratchet-type approach to building good
> evaluation for the fitness signal." ⇒ a loop UPSTREAM of the solution-ratchet: *propose a check →
> score how good the check is → revise → repeat.* The check is the reusable asset; the solve is
> disposable. PRD §13 (`research/selfdistill/PRD_internalized_ratchet.md`, synced to the live copy).
> - **THE CRUX = fitness-of-a-fitness-signal WITHOUT ground truth** (the off-benchmark case). Answer =
>   **discrimination/mutation**: a good check ACCEPTS a correct solution and REJECTS broken ones;
>   specificity-under-mutation needs no ground truth. tbench's official checker flips from "the thing we
>   climb" to "the VALIDATOR that mutation-specificity predicts real false-pass."
> - **① SPECIFICITY (GT-free) MEASURED:** `selfcheck/check_specificity.sh` runs each authored check vs
>   an unsolved and an emptied `/app` (known-negatives it MUST reject). **8/8 checks kill both mutants**
>   → none are the trivially-degenerate `exit 0` type. Necessary, NOT sufficient: `fix-git`'s check aces
>   specificity yet is path-fragile (greps bare `_includes/...` while the repo is at
>   `/app/personal-site/`) → would false-FAIL a correct solve. So the old probe's "0% false-pass, 8/8
>   agree" headline was HOLLOW (7/8 were trivial both-fail; the dangerous self✓/true✗ cell was empty).
> - **② `check_ratchet.sh` populates false-pass, then RATCHETS the check.** Author check (frozen) →
>   ratchet-solve capturing a docker snapshot at EVERY official-fitness improvement
>   (`ckr_<task>:f<passed>`, tests deleted pre-commit = anti-cheat clean) = a LABELED solution spectrum
>   (true-pass + near-misses) → score the frozen check against each (populates false-pass) → META step:
>   on any false-pass, tell the model which requirement-count it wrongly accepted, revise the check,
>   re-score (`fitness_v1→v2`). Outputs `check_ratchet/check_ratchet_{results,summary}.csv`.
>   **Live seed (tmux `ckr_run`):** `fix-git` authored 665B, solved 2/2 r1, self✓=true✓, fp=0 (pipeline
>   validated); `video-processing` climbing 2/5→3/5 (building the near-miss band) — v1/v2 pending.
> - **③ `graded_ratchet.sh` — apply it to the HARD flatlines (operator's extension).** Hard tasks
>   flatline because the official checker is one monolithic test (`total=1`) ⇒ binary ⇒ NO gradient.
>   The self-authored check is TASK-SPECIFIC: decompose the spec into N observable sub-requirements =
>   N gradient rungs. Model authors a **decomposed graded scorecard** (`SCORE=<p>/<t>`); the ratchet
>   climbs THAT. **Reward-hack guard:** the official checker never leaves — demoted to an ORACLE scored
>   every round, NEVER shown to the model. Verdicts: `SOLVED-via-gradient` · `gradient-TRACKS` ·
>   `PROXY-DIVERGED-rewardhack` · `stuck`. Built + registered; launches on the freed lane once the seed
>   validates (small steps: can't harden a hard graded check if it can't harden an easy boolean one).
> - **FLEET MECHANICS (reversible):** freed ONE .20 lane by stopping `wrk_20b` (tmux kill-session —
>   NEVER pkill -f) + clearing its orphan container; `check_ratchet.sh`+`graded_ratchet.sh` registered
>   in `fleet_supervisor.sh::experiment_active()` so the supervisor defers keepalive (won't relaunch
>   20b) and AUTO-RESUMES the campaign when the experiment exits. Campaign keeps 2 lanes (20a=.20,
>   21a=.21); the 28%→71% baseline delta is untouched. **LAND-MINE:** `tmux new-session -d` inherits the
>   tmux SERVER's cwd, not the shell's — use ABSOLUTE paths in the launched command (cost me 3 launches).
>   See [[project_meta_ratchet_fitness_signal]].

> **📉→📈 2026-08-29/30 — REAL BUT SMALLER GAIN: measured end-to-end 26.1% → 29.5% (+3.4 pts).
> The 35.2% I first reported was INFLATED. DPO = clean negative. Full detail: PRD §16.**
> - **⚠️ CORRECTION FIRST:** 35.2% came from UNIONING two separate sweeps (baseline-solved from one,
>   scaffold-solved from another). Unioning independent stochastic runs overstates the total
>   (max-of-two-noisy-measurements). Measured as ONE policy (`twopass_solve.sh`): pass1 **23/88 =
>   26.1%** -> after scaffold rescue **26/88 = 29.5%**, **+3 tasks**, rescue rate **3/65 = 4.6%**.
>   The +3 is EXACT (same run, losses structurally impossible); the 4.6% RATE is what may not
>   generalize. Pass1's 26.1% vs the reference 28.4% = run-to-run variance (~2 tasks) — a useful
>   calibration on single-sweep noise. **3rd correction this week, all from tightening method.**
> - **✅ CONDITIONAL SCAFFOLD (operator's first-principles framework, used as a RESCUE not a default):**
>   | strategy | solved (n=88) | |
>   |---|---|---|
>   | baseline (plain, 1 attempt) | 25 | 28.4% |
>   | scaffold ALWAYS | 28 | 31.8% |
>   | **plain first, scaffold ONLY on failure** | **31** | **35.2%** |
>   GAINS (6): constraints-scheduling · crack-7z-hash · fix-code-vulnerability ·
>   model-extraction-relu-logits · pytorch-model-cli · reshard-c4-data.
>   LOSSES (3): bn-fit-modify · mteb-retrieve · mteb-leaderboard.
>   **The conditionality IS the finding:** applied globally it only nets +3.4 because it BREAKS tasks
>   the model already solved (overthinking working code); applied only after a plain attempt fails it
>   nets **+6.8** and the losses become structurally impossible. No oracle, no training; 2 attempts fits
>   inside the k=5 budget. **Also explains the earlier n=8 "flatline win"** — that sample was drawn
>   entirely from baseline-failing tasks, i.e. the favourable subset, making a conditional effect look
>   general. **LIMITS:** 1 run/task (6-vs-3 carries noise) and the conditional figure is CONSTRUCTED
>   from two separate sweeps → `twopass_sweep.sh` now measures it end-to-end.
> - **❌ DPO ON CONTRASTIVE PAIRS = CHANCE.** Held-out preference accuracy **0.479 → 0.521 → 0.479**
>   (0.5 = chance); held-out loss **0.86 → 1.66 → 2.35** (rising ⇒ overfitting inside ONE epoch).
>   Training-side accuracy bounced 0.40–0.75 = noise, which is exactly why the held-out number was
>   instrumented. ⇒ **self-distillation is 0 for 5** (heredity v1/v2, MoE→31B, DPO). **The §14 recipe
>   fix paid for itself:** old settings would have burned ~18 epochs to loss 0.01 + ~15h of eval to
>   learn nothing; instead an unambiguous answer in 8h WITH a diagnosis.
> - **DPO v2 (diagnosed, not guessed):** (a) **margin too weak** — 604/735 pairs are margin=1 (one
>   check, plausibly luck); only 131 are ≥2. (b) **window in the wrong place** — `dpo_prep.py` takes the
>   FIRST 8 turns, but paired attempts share a `base_ref` and START NEARLY IDENTICAL; if they diverge at
>   turn 20 we trained on the SAME tokens with OPPOSITE labels, which forces chance. ⇒ v2 = **margin ≥2,
>   windowed at the DIVERGENCE POINT**.
> - **Threads (operator: keep all three):** ratchet WORKS (campaign continues) · eratchet dropped ·
>   self-distill 0/5 but now diagnosed → v2 · prompting is the only gaining thread (+6.8).

> **⚠️✅ 2026-08-28 — PAWL ABLATION CORRECTED (temperature was a confound) + FIRST PROTOCOL-LEGAL
> GAINS on the honest number. Full detail: PRD §15.**
> - **⚠️ CORRECTION — the 08-27 ablation claim was CONFOUNDED and is REVISED.** best-of-N ran at
>   drydock's flat default `temperature=0.2` while the RATCHET explores a ladder (0.2 exploit →
>   0.5/0.6/0.7/0.85, `ratchet.py::variant_specs`) ⇒ the control got **8 near-duplicate samples**, a
>   THIRD variable credited to the pawl. The tell was misread as signal: verbatim-repeating scores
>   (`0/2`×7, `1/2`×5) = duplicate sampling, NOT "a hard ceiling". Re-ran with a matched ladder:
>   | task | flat best-of-8 | temp-diversified |
>   |---|---|---|
>   | break-filter-js-from-html | 0/1 | **1/1 SOLVED** |
>   | filter-js-from-html | 1/2 | **2/2 SOLVED** |
>   | caffe-cifar-10 | 2/6 | 3/6 |
>   | extract-moves-from-video | 0/2 | 0/2 |
>   **flat 0/4 → temp 2/4; temp arm 2/8 overall.** ⇒ **REVISED: cumulative selection beats
>   diversified best-of-8 9 vs 2, NOT 9 vs 0.** The pawl still does most of the work but ~25% of its
>   apparent edge was sampling diversity. My "fresh attempts hit a ceiling more attempts don't raise"
>   claim is **WITHDRAWN**. Process lesson (2nd time): enumerate what differs between arms BEFORE
>   reading results. Flat arm stopped at 4/9 once established (documented, not silently truncated).
> - **✅ FIRST-PRINCIPLES SCAFFOLD (operator's framework) — the COMPRESSED version wins and cracked a
>   flatline.** 3 arms × 8 flatlines, single attempt, honest protocol, metric = `probe_fitness` 0–4
>   (binary-solved has NO dynamic range at 0/N — §14's floored-ruler lesson applied prospectively):
>   | arm | mean probe | solved | wrote CRITERIA.md |
>   |---|---|---|---|
>   | control (research nudge) | 0.50 | 0 | 0/8 |
>   | **compact (5 questions)** | **1.00** | **1** | **7/8** |
>   | full (10 steps) | 0.88 | 0 | 3/8 |
>   **`polyglot-c-py` (a measured `stuck` flatline, 0/1 over 8 graded rounds) SOLVED by the compact
>   scaffold** — prompt-only, no training, no oracle. compact DOUBLED control's probe. Pre-registered
>   `compact ≥ control > full`; actual `compact > full > control`. **Compliance explains it:** compact
>   wrote CRITERIA.md 7/8 vs full 3/8 — the longer framework is FOLLOWED LESS (analysis paralysis).
> - **🎯 CONVERGENT LEVER — externalize "done" BEFORE solving.** Three independent signals: self-ratchet
>   solved 2/5 with **`iters=0`** (loop never ran ⇒ the gain was the check-AUTHORING turn) · compact
>   scaffold (writes CRITERIA.md first) beat both arms · criteria-compliance tracks the winner.
>   **Protocol-legal solves on tasks the honest baseline FAILED = 3**: `cancel-async-tasks`,
>   `distribution-search` (self-ratchet), `polyglot-c-py` (compact). **Caveat: small n — 1/8 is within
>   noise alone; the claim rests on CONVERGENCE of three cheap experiments.** Next: compact scaffold
>   over the full 89 vs a matched baseline.
> - **⚡ vLLM ADOPTED for experiments (measured):** `.21` vLLM **24.2 / 48.7 / 48.5** tok/s at conc
>   1/4/8 vs `.20` llama.cpp **12.9 / 17.7 / 18.1** ⇒ **~2.8× at conc=4**; llama.cpp flatlines (`-np 2`).
>   **COMPARABILITY GUARD:** different QUANTS — `.20` `…-UD-Q4_K_XL` (gguf, where the **28.4% reference**
>   was measured) vs `.21` `…-qat-w4a16-ct` ⇒ vLLM numbers are NOT comparable to 28.4%. So:
>   `baseline_sweep.sh` gained an `OUT_DIR` override (vLLM run **cannot overwrite** the reference CSV),
>   a **vLLM re-baseline is running** (89×k=1, 4 streams) to set the new yardstick, and the temp ablation
>   deliberately STAYED on `.20` to stay comparable to its own flat arm.
> - **LAND-MINE:** DPO died silently — launched via `nohup … &` from the supervisor subshell, not tmux
>   (the documented failure mode). Relaunched as tmux `ckr_dpo`. **Launch long jobs in tmux, always.**

> **🔧 2026-08-27 — "BEAT TBENCH HONESTLY": eratchet DROPPED · self-distill diagnosed (we trained
> memorizers + measured with a floored ruler) · 3 experiments in flight.**
> Honest target restated: the leaderboard-comparable number is the **no-score-feedback protocol =
> 28.4%**, NOT the 70.8% search figure. Everything below is legal under that protocol. Full detail:
> PRD §14 (diagnosis) and §13.5 (fitness-signal results).
> - **WHY THE RATCHET WORKS (quantified, from log mining).** 39 task-runs with round-level fitness:
>   **12 of 13 solves happened at round ≥2** (only ONE at round 1) and **51% of runs improved best
>   fitness after round 1**. Mechanism: best-of-N with fresh containers must get all k sub-goals right
>   **simultaneously** (`q^k`), while the ratchet needs each sub-goal to succeed **once, ever**
>   (`(1-(1-q)^N)^k`) — at q=0.4,k=5,N=8 that is **7.9% vs 91.9%**. The **pawl** (snapshot on
>   improvement, `restore` on regression) forbids backward motion, turning a high-variance random walk
>   into a monotone climb; fine-grained fitness (dead→crashed→asserted→pass) just adds more teeth.
> - **⚠️ THE MISSING ABLATION, NOW RUNNING (`bestofn_ablation.sh`, tmux `ckr_bon_a/b`).** 28→71
>   confounds *more attempts* with *cumulative selection* — best-of-N-without-ratchet was NEVER run.
>   Set = **9 tasks where honest k=1 FAILED and the ratchet solved at round ≥2**
>   (break-filter-js-from-html 5 · caffe-cifar-10 7 · extract-moves-from-video 6 · filter-js-from-html 7
>   · fix-ocaml-gc 4 · gcode-to-text 9 · gpt2-codegolf 14 · raman-fitting 3 · rstan-to-pystan 6).
>   Arm = N=8 INDEPENDENT fresh attempts, no state/rollback/feedback, early-exit on first solve, 2
>   streams. **Read: 0–2/9 ⇒ the PAWL is the mechanism; 6–9/9 ⇒ it was mostly just more attempts.**
> - **🚫 ERATCHET DROPPED (operator decision).** Post-`commit_ref`-fix it has never cracked a task the
>   plain ratchet couldn't, costs ~3×/round, and its QD niching is structurally impotent at 0/N.
>   Keep the PLAIN ratchet.
> - **🔬 SELF-DISTILL DIAGNOSIS — two mechanical causes never controlled for.** (a) **We trained
>   memorizers:** `accum=8`⇒eff-batch 8, so heredity v1 = **~16 epochs**, v2 = **~18 epochs** on 30–53
>   windows → loss **0.016 / 0.009** (verbatim memorization; MoE at ~5 epochs landed 0.12 — loss tracks
>   epochs exactly). Standard is 2–3 epochs. Epochs were never logged, so nobody saw it. (b) **THE RULER
>   IS FLOORED:** every verdict used BINARY SOLVES on held-out sets where **base = 0/6** (base is 0/30
>   even multi-round) — an instrument that reads 0 for base reads 0 for an improved model too. It
>   detected *harm* (reg 1→0, −9 checks) but **could never have detected a gain** ⇒ "zero transfer" and
>   "unmeasurable" are indistinguishable in the existing data. Ruled OUT: loss masking is already
>   correct (prompt tokens `-100`), so boilerplate-gradient is NOT the problem.
> - **FIXES SHIPPED:** **`graded_eval.sh`** = the new ruler — metric is graded **CHECKS PASSED** over
>   the **near-miss band** (baseline partial>0 & unsolved); measured **base = 52/109 = 47.7% over 23
>   tasks** ⇒ real headroom both directions. **`dpo_train_31b.py`** now holds out 10%, **logs EPOCHS**
>   (warns >4), evals on the split, prints an OVERFIT WARNING when eval loss rises, and reports
>   **held-out preference accuracy** (0.5 = chance). Also fixed trl 1.12 dropping `max_prompt_length`.
> - **SELF-RATCHET (`selfratchet_solve.sh`) — protocol-legal eval-time self-verification**, and a real
>   bug it exposed. First smoke left the frozen check readable at `/app/selfcheck.sh` AND told the model
>   to make it pass → **4/5 false-pass** (self✓ while official ✗), `iters=0` everywhere (the loop never
>   engaged). That is **in-context Goodhart with a clean control**: same model/checks/tasks scored
>   **0 false-pass when the check was INDEPENDENT** (§13.5) vs 4/5 when the solver could read its own
>   criterion. Fixed: check removed from the container, only failure OUTPUT returned. **Honest caveat:**
>   re-run still shows `llm-inference-batching-scheduler` self-passing at official 5/6 ⇒ independence
>   removes the *incentive* to game but does NOT guarantee the check captures every official
>   requirement — a self-authored check can be genuinely incomplete. Archived control arm:
>   `selfratchet_results.visible-check-BROKEN.csv`.
> - **IN FLIGHT:** `ckr_selfr` (hidden-check smoke) → `ckr_bon_a/b` (the pawl ablation) → DPO
>   (483 pairs, queued; its wait-loop knows all experiment session names). See
>   [[project_meta_ratchet_fitness_signal]].

> **📊 2026-08-26 — RESULTS IN: self-authored checks err STRICT (0 false-pass, n=6); graded signal cracked
> a flatline honestly. Now scaling the graded batch.**
> Two threads closed cleanly (full detail: PRD §13.5).
> - **(A) Do self-authored checks ever FALSE-PASS? NO — across every provocation.** specificity
>   (unsolved/empty) **0/8** · honest near-miss (2/5–4/5, well-formed, fails ≥1 req) **0/4** · adversarial
>   decoy (truncate-to-empty + same-size garble, `check_decoy_probe.sh`) **0/4**. ⇒ on this distribution the
>   checks **err STRICT, not lax**: they verify actual CONTENT (reject emptied AND garbled outputs), catch a
>   well-formed solution missing one requirement, and never accept a wrong one. For SEARCH this is the SAFE
>   asymmetry (residual risk = false-FAIL wasted effort, never false-PASS reward-hack). **Honest limits:** the
>   **revise loop (`v1→v2`) is built but NEVER FIRED** (no false-pass to fix — "not needed on 6 tasks," NOT
>   "proven to work"); checks lean over-strict (only `fix-git` confirmed to ACCEPT a true solve, and its check
>   was path-fragile); the subtly-wrong-VALUE band (valid format, wrong number) stays untested (truncate/garble
>   too blunt). n=6, one model.
> - **(B) Can a self-authored GRADED signal manufacture gradient on a flatline WITHOUT reward-hacking? SCALED
>   to n=11 (COMPLETE) — rarely, it often lies, and the ORACLE is what makes it safe.** Distribution:
>   **1/11 honest crack** (regex-chess `0/4→1/4`) · **2/11 REWARD-HACKED, oracle caught both**
>   (make-doom-for-mips auth 2→4/7 vs off `0/3`; winning-avg-corewars auth 4→5/7 vs off `2/3`) · **5/11
>   `stuck`** (authored score itself never climbed) · **3/11 `no-card`** (couldn't author). Honest cracks =
>   **9%** ⇒ "graded signals crack flatlines" DECISIVELY NOT supported. **Load-bearing = the DETECTOR:** on
>   2/2 tasks where the authored score rose without real progress, the held-out oracle flagged
>   `PROXY-DIVERGED` — a genuine reward-hack caught in the wild, twice, independently.
> - **FAILURE-MODE TAXONOMY (the real finding):** hard-task resistance decomposes into 3 distinct
>   bottlenecks — **authoring** failure (`no-card`, 3) · **capability** failure (`stuck`, signal fine but
>   solver can't progress, 5) · **validity** failure (`reward-hack`, oracle catches, 2) · genuine (1).
>   Collection COMPLETE; next = WRITEUP (phenomenon + detector + taxonomy), not more batches.
> - **⇒ THIS REVISES (A):** (A)'s "checks err strict, never false-pass" held only on tasks the model
>   UNDERSTANDS; at the competence frontier (`make-doom-for-mips`) it authored a scorecard it could satisfy
>   WITHOUT solving. **Trustworthiness of a self-authored fitness signal degrades with task difficulty — safe
>   where the model understands, reward-hackable exactly where it doesn't, and search is most needed where
>   understanding is weakest.** That tension is why the oracle is not optional (publishable core = reward-hacking
>   of self-authored proxies + a working detector, NOT a crack/SOTA claim). Scaling further: batch-2 (tmux
>   `ckr_graded`) = polyglot-rust-c · feal-differential · make-mips-interpreter · winning-avg-corewars ·
>   qemu-alpine-ssh · dna-insert, to tighten the crack/reward-hack/no-signal split.
> - **⇒ PIVOT (decided):** the false-pass hunt on CHECKS is DONE (conservative on easy tasks; the real
>   false-pass risk showed up as graded-scorecard DIVERGENCE on hard tasks, above). The live value is thread (B).
>   Same freed-lane discipline (`check_decoy_probe.sh`+`graded_ratchet.sh` in `experiment_active()`; stop 20b by
>   tmux; auto-resume on exit). Campaign untouched at 63/89. See [[project_meta_ratchet_fitness_signal]].

> **🧠 2026-08-23 (later) — STRATEGY REFRAME + the generalization question, made measurable.**
> Three threads converged today. Read this block before deciding what the project IS.
> - **① REFRAME: the claim is SEARCH-AS-CAPABILITY-SUBSTITUTION, not benchmark placement.** DeepSeek reports
>   V4-Pro **87.9 on TB 2.1** — above the #1 public entry (83.8) — and reports it with the harness in
>   **"minimal mode"**. That detail is the whole insight: **a strong model needs little scaffolding; a weak
>   one needs a lot.** Our data is the same curve from the other end (a 31B needing ~48 attempts + verifier
>   feedback to reach 70.8%). So the durable research question is NOT "can a 31B compete" (it can't, and the
>   frontier moves monthly) but **"how much capability can test-time search buy, and where does that
>   substitution stop paying?"** That framing survives frontier progress; a placement claim does not.
> - **② DeepSeek-harness teardown (cloned, read — not the hype articles).** 2472 TS files, `packages/`:
>   acp api compaction context goal **guard** hooks jobs llm lsp mcp plan sandbox session **skill** spill
>   **subagent** terminal **todo** workflow workspace. **Drydock independently converged on ~the same
>   subsystem decomposition** (compaction, loop-guards, jobs, skills, todo/plan, subagents, MCP, sandbox);
>   difference is packaging (their swappable plugins vs our flat modules), not conception. **Their
>   `repeat-tool-reminder` is nearly line-for-line our `loop_detect.py`** — *"advisory loop-breaker… never
>   vetoes or rewrites a call… the decision stays entirely with the model"*, escalating thresholds [3,5,8]
>   vs our 2/3/5+. Two teams, same answer ⇒ the **advisory-never-blocking rule is validated**. Their
>   `timeout-policy` = the idle-watchdog we shipped 08-21.
>   **THE GAP THAT MATTERS: they do NO test-time search.** Grepped `packages/core/agent` +
>   `packages/subagent` for checker/verifier/score-retry/rollback — **nothing**. A SOTA harness leaves that
>   layer empty ⇒ the ratchet is our distinct contribution; we should NOT compete on harness features
>   (converged) or distribution (unwinnable — 95k stars in 2 days is a brand/launch outcome).
>   Also: their **`BENCHMARK.md` is a 3-line stub** — the 87.9 has no published protocol, so **our full
>   protocol + ablations will be MORE reproducible than theirs**. One line is still useful evidence for us:
>   *"use separate workspaces and session IDs for independent benchmark tasks"* = per-task isolation, the
>   opposite of our state-carrying snapshot/rollback — independent confirmation our number isn't comparable.
> - **③ DOES THE SCAFFOLDING HELP ORDINARY WORK? (the operator's question) — separate the two levers.**
>   **The harness** needs no verifier and transfers unconditionally (evidence: `base_solved.txt` ~10 is a
>   known undercount once driven through the real TUI + research nudge). **The search** needs a fitness
>   signal — but "hidden benchmark checker" is only ONE source. Already shipped for the everyday case:
>   `drydock/ratchet.py::Verifier` = *"the USER'S own tests/build/lint"* + `detect_verifier()`
>   (cargo/pytest/npm/make), so bare `/ratchet <goal>` works in a real repo. **Fitness ladder when there are
>   no tests:** (a) compile the stated goal into an executable check, authored BEFORE the solution and
>   human-approved ONCE (cheap: approve 1 check, not 48 attempts); (b) quantitative goals score themselves
>   (ms, bytes, lint count, type coverage — CONTINUOUS gradients, better than binary); (c) **differential /
>   behavioural-equivalence** (run old vs new, diff outputs — the best trick for refactors and bug-fixes, no
>   spec needed); (d) one golden example → distance; (e) property/metamorphic invariants; (f) the
>   checker-independent `probe_fitness.sh` floor. **Risk to state plainly: search optimizes proxies
>   ruthlessly — weak check + strong search = confident wrongness (reward hacking).** LLM-as-judge is
>   self-grading and gameable: fine as a tiebreaker, bad as the thing you climb.
> - **④ MEASUREMENT GAP FOUND:** the running baseline is single-attempt **WITH** Drydock, so harness value is
>   baked in and invisible. Planned 3rd arm (same 89, single attempt, MINIMAL scaffolding) decomposes it:
>   **raw model → +harness → +search**. That is a much stronger paper than the 2-point version.
> - **⑤ NEW EXPERIMENT QUEUED — `selfcheck_probe.sh` (turn the benchmark into a testbed for the NO-benchmark
>   case).** Operator's idea: have the agent write its OWN scorecard from `instruction.md` alone, then score
>   the same solution with BOTH it and the official checker. **Headline = FALSE-PASS rate** (self✓ true✗) =
>   exactly how far a self-authored target can be trusted as a SEARCH objective. Validity guards:
>   (1) **no leakage** — verified `ddt_up` never copies `/tests` (only `ddt_verify`, at scoring time);
>   (2) **frozen + ordered** — check written BEFORE any solution and copied to the host, solve runs in a
>   FRESH container so it can't be reverse-engineered or edited to fit; (3) **same solver** as
>   `control_noratchet.sh`, so it measures CHECK quality not a different solver. 12 tasks spanning
>   easy/near-miss/hard, queued behind the baseline. Expect false-FAIL to be common (agent checks over-assert);
>   false-PASS is the real finding. `selfcheck_report.py` prints the confusion matrix.
> - **⑥ PUBLICATION STRATEGY (decided).** **No exclusivity / no "first shot" to a lab** — it costs the public
>   timestamp, independent readers, and provenance, and buys nothing. **Publish openly** (arXiv or clean
>   writeup + reproducible repo), ask for nothing, engage on substance. If a specific lab is the goal, apply
>   through normal channels and let the artifact be the evidence; a private research email is a worse version
>   of both. **The paper is systems + rigorous NEGATIVE results** (heredity 0/6 transfer ×2 · MoE→31B null +
>   regression · winner-only corpora can't teach recovery · binary pass/fail destroys the search gradient)
>   **plus the delta** — NOT a SOTA claim, which would be debunked on the first protocol question.
>   **Ablations are load-bearing**: `control_noratchet.sh`, `FINE_FITNESS=0/1`, plain-vs-eratchet.
> - **LIVE NUMBERS 09:08:** baseline **7/89 done**, interim **28.6%** (CI [8.2, 64.1] — far too early to read),
>   campaign **63/89**. Claim shape the report prints: *"…lifts Gemma-4-31B from X% to 71% on TB 2.1"*.

> **🔬 2026-08-23 (later) — SELFCHECK PROBE WAS SILENTLY NO-OP'ing; FIXED + re-running. First real
> data point: self-authored check AGREES with ground truth on fix-git.**
> The self-authored-scorecard experiment (item ⑤ below) — *can the model write its own fitness
> signal off-benchmark?* — ran once and produced **0 usable rows (12/12 `NA`)**. Root cause: the
> probe's `drive()` captured the TUI pane BEFORE its first sleep and grepped a bare `ready`, so it
> matched the idle status line on loop iteration 1 and returned in ~17s — the agent never authored
> `/app/selfcheck.sh`. **Fixed** to `control_noratchet.sh`'s proven wait logic (sleep-at-top +
> `⚓ (working|ready)` glyph anchor); applied to live + vendored copies; committed **`0d93379`**.
> Validated end-to-end: `fix-git` now authors a concrete **1076B** check (branch + conflict markers +
> exact expected strings) instead of 0B. **First row: `fix-git self=1 true=1 (2/2)`** — solved AND
> the self-check AGREED with the official checker, erring toward over-strict (the SAFE direction for a
> search objective: false-FAIL costs only efficiency, false-PASS reward-hacks into wrongness). Sweep
> re-running the balanced 12 (4 true-pass / 4 near-miss / 4 flatline) on .20; `openssl`+`nginx` checks
> already authored (3.3K/3.9K). **Headline pending = the FALSE-PASS rate** (`selfcheck_report.py`
> confusion matrix). **LAND-MINE re-hit + logged:** my relaunch pipeline's `while pgrep -f
> "selfcheck_probe.sh fix-git"` self-matched its own wrapper argv and hung after the probe finished —
> killed by exact PID, never `pkill -f`. PRD `PRD_internalized_ratchet.md` §12 updated with both
> results (baseline = the internalization TARGET quantified; selfcheck = the off-benchmark verifier).
>
> **📏 2026-08-23 (DONE) — HONEST BASELINE MEASURED = 28.4%; the delta is real: +42.4pts / 2.49×.**
> `baseline_sweep.sh` finished all 89 (88 scored, 1 err): **single-attempt, no ratchet, no score feedback =
> 25/88 solved = 28.4% (Wilson 95% CI [20.0, 38.6])** vs the verifier-guided campaign **63/89 = 70.8%**.
> So X landed BELOW the 35–45% guess — which makes the delta *stronger*: **"verifier-guided test-time search
> lifts Gemma-4-31B from 28% to 71% on Terminal-Bench 2.1" (2.49×)**, standing entirely on its own with no
> leaderboard comparison. `baseline_report.py` prints the claim shape. This is the comparable quantity; the 71%
> is NOT (score feedback + ~48 attempts/task). Old `base_solved.txt` ~11% confirmed a large undercount.
>
> **📏 2026-08-23 — THE HONEST BASELINE (running): 63/89 is NOT leaderboard-comparable; measure the DELTA.**
> Context: tbench-2.1 leaderboard shows #10 Claude Code/Sonnet-5 **74.6%**, #12 Claude Code/Opus-4.7 **68.9%**,
> so our **63/89 = 70.8%** *looks* like ~11th. **It is not the same measurement, and must never be published
> as one.** Submissions run `harbor run … -k 5` = **5 INDEPENDENT fresh attempts, no score feedback**. Our
> number comes from up to **16 rounds × 2–3 variants (~48 attempts/task)**, snapshot/rollback carrying state
> BETWEEN attempts, tasks re-queued at escalating tiers over WEEKS, and — decisively — **the hidden checker's
> score fed back into the next prompt** ("you pass 2/3, advance without breaking"). We never show the model
> the TESTS (anti-cheat deletes `/tests` pre-snapshot) but we do show it the SCORE and select on it across
> ~48 attempts: that is **test-time RL against the evaluation metric**. Legitimate technique, non-comparable
> number. Claiming ~70% vs the leaderboard would be debunked on the first protocol question — and given the
> v2 quarantine + the 3.1.16 phone-home incident, a debunked benchmark claim is the one cost this project
> cannot pay twice.
> - **⇒ MEASURING THE COMPARABLE QUANTITY.** `baseline_sweep.sh` + `baseline_report.py`: **ONE attempt per
>   task**, no rounds/snapshot/rollback/continuation, **no score feedback** (`control_noratchet.sh`, which
>   uses the SAME research nudge as ratchet round 1 — a fair control, not a handicap). Running now: **89
>   tasks × k=1 across 2 streams on .20** (~7h), campaign keeps **1 lane (21a)**.
> - **STATS NOTE (corrected a bad first instinct):** for an AGGREGATE pass rate, task-to-task variance
>   dominates run-to-run variance, so **k=1 over all 89 (89 runs, ~±5%)** estimates the mean BETTER than
>   k=5 over 25 tasks (125 runs, ~±9%). Fewer runs *and* a tighter number. Raise K later for publication-grade
>   per-task rates. Report uses a **Wilson** interval (well-behaved at small n / extreme p).
> - **THE PUBLISHABLE CLAIM** is the delta: *"Drydock + verifier-guided test-time search lifts Gemma-4-31B
>   from X% to 70.8% on Terminal-Bench 2.1."* X is unknown — the old `base_solved.txt` ~10 tasks (~11%) is a
>   known UNDERCOUNT (predates driving through the real TUI + research nudge; many later solves were round-1).
>   If X lands 35–45%, "search nearly doubles a 31B's agentic capability" stands on its own with no comparison
>   games. **The paper is systems + rigorous NEGATIVE results** (heredity 0/6 transfer ×2; MoE→31B null+
>   regression; winner-only corpora can't teach recovery; binary pass/fail destroys the gradient search needs)
>   — not a SOTA claim. Ablation switches already exist: `control_noratchet.sh`, `FINE_FITNESS=0/1`, plain-vs-eratchet.
> - Files: `baseline_sweep.sh` (resumable, flock'd CSV, container-collision guard), `baseline_report.py`,
>   `baseline/baseline_results.csv`. `baseline_sweep.sh` added to `experiment_active()` so the supervisor
>   won't relaunch the freed .20 workers. Their 2 in-flight jobs were closed as
>   `orphaned-by-baseline-reallocation` and re-queued (NOT scored as task failures — that would feed false
>   flatline evidence to the auto-park).

> **🧭 2026-08-22 — MANUFACTURING GRADIENT WHERE THERE IS NONE (3 levers shipped).** Operator: "how do we
> produce a gradient where there isn't one? … we need a list of logic solving patterns."
> **Diagnosis first — measured, not assumed.** ~140 of 175 captured rows were PURE flatlines (polyglot-c-py
> 31× `0/1`, polyglot-rust-c 31× `0/1`, protein-assembly 26× `0/1`, qemu-alpine-ssh 21× `0/1`, regex-chess
> 26× `0/4`); only `path-tracing-reverse` had gradient. Several are `total=1` — a single monolithic test, so
> the score is structurally BINARY. Campaign frozen 60/89 ~20h while near-misses (**sam-cell-seg 8/9**,
> video-processing 4/5, path-tracing 4/5) sat PENDING but unreached, because a tier-3 run holds a lane ~8h.
> - **ROOT GAP FOUND: eratchet was throwing the fine gradient away.** `ctrf_fitness.py` (built 08-07) grades
>   each failing check on a sub-goal ladder — **dead 0 → crashed .33 → asserted .66 → pass 1** — but only the
>   PLAIN ratchet used it. eratchet did its own inline parse keeping just `(passed,total,names)`, i.e. the
>   COARSE binary signal, on exactly the hard tier-3 residue where gradient is scarcest. **Now wired in**
>   (same parser, no logic drift). Verified: `call_failed`/`failed`→330, assertion→660, collection error→0.
> - **LEVER 2 — TIER-C PROXY GRADIENT (`probe_fitness.sh`), independent of the checker.** The checker ladder
>   collapses to a flat 0 when the solution never runs — no information exactly where we need it. The probe
>   scores 0–4 from milestones observable BEFORE any test passes: wrote source → syntactically valid
>   (py_compile/gcc/cargo/node) → an entry point RUNS without traceback → produced an output artifact.
>   Deterministic, inspects `/app` ONLY (never `/tests`, never the checker) ⇒ anti-cheat clean, no LLM judge.
> - **LEVER 3 — LOGIC-SOLVING PATTERN LADDER + strategy-as-NICHE.** eratchet's modes
>   (continue/rethink/diversify/fanout) are SEARCH-CONTROL knobs — they set how far to jump, not WHAT to try,
>   so variants explored the same logic at different temperatures. Added 10 distinct approaches (SPEC-LITERAL,
>   WORK-BACKWARDS, ENVIRONMENT-FIRST, MINIMAL-REPRO, REFERENCE-IMPL, INSTRUMENT, BRUTE-FORCE-FIRST, DECOMPOSE,
>   INVARIANT-CHECK, BISECT), rotated deterministically over (round, variant) — verified to spread across 9
>   distinct strategies in 3×3. **The deeper fix:** QD keys niches on WHICH checks pass, so at 0/N every
>   variant has an empty descriptor → **always exactly one niche** → `diversify`/`fanout` are structurally
>   impotent. That is *why* flatlines never diversify. The strategy tag now becomes the descriptor when no
>   check passes, giving the archive an axis to spread along.
> - **Fitness is now lexicographic (passed, partial, probe)**; the flatline early-abort only counts a round
>   flat when NOTHING moved on any axis (else a task climbing dead→crashed→asserting behind a constant `0/N`
>   would be cut). Round logs now print `partial=` and `probe=` — without them we could not tell a genuinely
>   dead task from one quietly making progress.
> - Files: `ratchet_evolve.sh`, `probe_fitness.sh`, `ctrf_fitness.py` (reused). All installed by **atomic mv**
>   (bash reads scripts lazily; in-flight jobs keep the old inode). Near-miss band seeded; `video-processing`
>   already picked up by a lane.

> **🔍 2026-08-21 — WHY IT FAILED (diagnosed, not guessed) + the FIX now collecting: CONTRASTIVE PAIRS.**
> Operator asked the right question: did it fail to pick up the RATCHET behavior, or was it poisoned by the
> 4B's weakness? **Evidence says: mostly the former — the ratchet behavior was never in the data.**
> - **10 of 14 traces solved at ROUND 1** (no across-round behavior existed at all); only **1 of 14** carried
>   cross-round continuation context. And `_solved_traj.json` is `docker cp .dd_trajectory.json` taken AT the
>   solve — each round runs a FRESH drydock session, so the file holds **only the winning round**. For the 4
>   multi-round solves the failed round and the recovery from it were OVERWRITTEN. ⇒ we trained on "here is a
>   successful attempt", never "here is how to preserve progress and improve". It could not learn what it was
>   never shown.
> - **Weakness is a real but SECONDARY factor:** 68 tool-failure results across 14 traces (~5/trace) and very
>   verbose solutions (git-multibranch **54** assistant turns, git-leak-recovery 46). Training a strong model
>   to imitate verbose flailing is a plausible mechanism for the regression — note `build-cython-ext` got
>   WORSE at something it nearly aced (10/11→3/11). Both factors, compounding; missing-signal dominant.
> - **⇒ THE FIX (built + LIVE): capture EVERY variant/round, not just the winner.** `drydock.eratchet`
>   already has this (`--capture` + `contrastive_pairs()`, docstring: "the RL/preference signal … that a
>   **winner-only corpus can't provide**") — but the FLEET's `ratchet_evolve.sh` is a separate docker/TUI loop
>   with the SAME winner-only flaw. Patched both fleet scripts to save each variant's transcript + score to
>   `ratchet/capture/` + `manifest.tsv` (env `CAPTURE_ROUNDS=1`, default on, additive). New
>   **`build_pairs.py`** groups by the SHARED `(task, base_ref)` and emits (chosen, rejected) pairs by margin.
> - **WHY THIS ALSO BEATS THE FRONTIER WALL:** a preference pair needs only *one attempt beat another* — **NOT
>   a solve**. So it produces signal on tasks the 31B FAILS, which is exactly where the headroom is, and it
>   comes from the 31B itself (no weak-model style to import). This sidesteps the "fast generator ⇒ easy tasks
>   ⇒ no headroom" trap the MoE verdict identified. Collection **rides along on the campaign's existing
>   eratchet runs** — no dedicated GPU time.
> - **SAFETY NOTE:** both scripts were live (worker 20b mid-run), and bash reads scripts LAZILY — an in-place
>   edit can make a running instance execute garbage. Patched a copy and **atomic `mv`** (new inode; the
>   running process keeps the old one). Verified the in-flight eratchet continued cleanly.

> **🏁 2026-08-21 — MoE→31B VERDICT: NULL on generalization + partial-credit REGRESSION. Do not promote.**
> Full cycle ran end-to-end (collect→harvest→train→convert→leakage-clean A/B eval). **Result:**
> | arm | generalization (31B FAILS, n=5) | regression (31B PASSES, n=5, leakage-clean) |
> |---|---|---|
> | base | **0/5 solved**, 4 checks | **1/5 solved**, **21 checks** |
> | +LoRA | **0/5 solved**, 4 checks | **1/5 solved**, **12 checks** |
> - **Generalization lift = 0** (0 solves gained; checks net **+0** — caffe +1, filter-js −1). No transfer up.
> - **Regression: no SOLVE lost** (`build-pmars` held 4/4 both arms) **but −9 checks**: `build-cython-ext`
>   **10/11 → 3/11 (−7)**, `bn-fit-modify` 2/9 → 0/9 (−2). One-directional: 2 drops, 0 gains.
> - **⇒ BOTH pre-registered risks landed on the bad side.** (1) **HEADROOM** — the easy tasks the MoE can
>   solve sit INSIDE the 31B's frontier, so there was nothing to teach; the low starting loss (**1.13** vs
>   heredity's 4.68/5.24) foreshadowed exactly this. (2) **DISTILLING DOWN** — training the strong 31B to
>   imitate a weak 4B-active model's solution style applied real downward pressure on work it already did well.
> - **THE STRUCTURAL TENSION (the real lesson):** the throughput logic was sound and every mechanical part
>   worked — MoE ran **59 tok/s (~4×)**, did NOT loop on easy tasks, and built 14 verified traces in ~5h vs the
>   31B's ~1–2 solves/day. But *fast generator ⟹ only easy tasks ⟹ no headroom*. To help the 31B the traces
>   must come from tasks the 31B FAILS — which the weaker MoE cannot solve either. **Speed doesn't buy
>   usable data; the binding constraint is the FRONTIER, not throughput.**
> - **Honest limits:** n=5/arm, single stochastic attempt, 14-trace corpus, 60 steps. Suggestive, NOT proof —
>   `build-cython-ext` −7 could be partly variance. A full 35/40 sweep would confirm. The cheap spot-check was
>   chosen precisely so a null cost ~4h instead of ~10h. The **self-trace control arm was NOT run** (it needs
>   ~1.5–2 days of 31B self-collection), so "cross-model specifically" isn't isolated from "small corpus".
> - Artifacts: `adapters/moe-easy-20260820-2147`, `Models/moe-easy-20260820-2147-lora.gguf` (245M),
>   `moe_collect/eval_results.csv`, `moe_eval.sh`. Fleet restored (.20 server back, .22 back to QAT
>   collection config, campaign resumed **57/89**). Loss curve: 1.1257 → **0.1215** (mean_last_10 0.2006) —
>   did NOT collapse to heredity's ~0.01, so this was not pure memorization either.

> **🧪 2026-08-20 — MoE-AS-FAST-GENERATOR EXPERIMENT (26B-A4B → 31B); SMOKE GATE PASSED, collecting.**
> Operator idea: self-distill data creation is throughput-starved (dense 31B = slow inference; hard
> tasks flatline = zero data). Use the fast **Gemma-4 26B-A4B MoE (~4B active)** as a high-throughput
> GENERATOR on EASIER problems → verified traces → train the mission's **31B dense** on them. Same
> Gemma-4 family ⇒ traces are format-native to the 31B. **Reframe: this is NOT self-distillation — it's
> a throughput hack (cross-model 26B→31B).** Honesty bar still holds: the 31B solves the eval itself;
> traces are verified. The MoE is *weaker*, so it's not a stronger-teacher crutch.
> - **THE GATE (why the MoE was benched in the first place): it loop-collapsed on hard tbench-2 with
>   only ~4B active** (180× identical pytest). Hypothesis: looping = out-of-depth; easy problems relieve
>   it. **VERIFIED PASS:** served MoE Q4_K_M on .22 (stopped the idle 31B there; restore via
>   `docker start llamacpp-gemma4-31b`), **59 tok/s vs 31B ~15 = ~4×**. Smoke through the REAL TUI with
>   working CTRF: `fix-git` SOLVED r1 **2/2** (matched the 31B, ~4× faster), `regex-log` SOLVED r2
>   (0/1→1/1 via the partial-credit climb). **No looping on easy tasks** (n=2, clean). Bug found+fixed:
>   a `/tmp` copy of the solver broke `$SD/ctrf_fitness.py` resolution → first pass scored a bogus 0/0.
> - **TWO RISKS still to MEASURE (the experiment's whole point):** (1) **HEADROOM** — easy tasks the MoE
>   solves are likely within the 31B's frontier → training on already-solved = no lift (the 07-21
>   lesson) UNLESS diverse easy traces GENERALIZE to hard held-out. (2) **DISTILLING-DOWN** — teaching
>   the strong 31B a weak 4B-active model's solution style could regress it. ⇒ eval MUST have a
>   **generalization arm** (held-out HARD tasks the 31B currently fails) + a **regression arm** (tasks it
>   passes) + a **control** (31B trained on its OWN easy traces) to isolate whether cross-model helps.
> - **COLLECT (done, expanding):** MoE ratcheted 8 easy tasks → **6 solved trajs** into `moe_collect/`
>   (SEPARATE from campaign `ratchet/`); an expanded 14-task collect is now growing it on the idle .22.
>   **Harvest works:** `moe_collect/moe_sft.jsonl` (full-trajectory chat SFT, ~170 assistant turns;
>   `_solved_traj.json` existence == verified — the inner `verified` field isn't stamped). **Eval splits
>   built** (`moe_collect/eval_splits.json`): regression=54 (31B passes), generalization=35 (31B fails).
>   **Scaffold + gating criteria:** `moe_collect/README.md`. **TRAIN + EVAL PENDING** a free trainer (.20
>   busy) AND a bigger corpus (heredity memorized at 16–31; 6 is too few). Eval = generalization arm +
>   regression guard + self-trace control.
> - **🧬 COLLECT DONE + A DIAGNOSTIC CORRECTION (2026-08-20): speciate on the INSTRUCTION, not the trace.**
>   MoE corpus = **14 verified solve-trajectories, 391 assistant turns** (cancel-async-tasks,
>   configure-git-webserver, fix-git, git-leak-recovery, git-multibranch, headless-terminal,
>   largest-eigenval, log-summary-date-ranges, nginx-request-logging, openssl-selfsigned-cert,
>   prove-plus-comm, regex-log, sqlite-with-gcov, vulnerable-secret). Ran the heredity failure-tell
>   diagnostic: `speciate.py` on the TRACE text → **1 species** (the same alarming signal that was read as
>   "memorizes format→solution"). But on the raw **INSTRUCTION** the SAME corpus → **5 species**
>   (git/config · security-crypto · log/regex · proofs · sqlite). **⇒ the "1 species" result is an ARTIFACT
>   of the uniform harness format** (identical system prompt + tool-call shape make every drydock trace
>   look alike), NOT a real diversity failure — so it cannot be evidence for format-memorization.
>   **This partially re-interprets the heredity verdict:** v1/v2 also clustered *condensed trace text*, so
>   their headline tell ("31 diverse tasks → 1 cluster") likely measured the same artifact. RESUME had
>   already flagged "cluster on the INSTRUCTION next" as lever (a) — this confirms it and shows the
>   instruction-space signal is the meaningful one. **Practical upshot:** this corpus has REAL semantic
>   diversity (5 species/14 tasks), so the experiment is not pre-doomed to the memorization failure; and
>   any future per-species adapter split should key on instruction-space clusters.
>   Diagnostic inputs: `moe_collect/moe_instructions.jsonl`, `moe_collect/moe_sft.jsonl`.
> - **🔁 SEQUENCED (2026-08-20, operator: "finish parking then free .20 to train"):** the 3 lanes were all
>   grinding flatlines (dna-assembly/dna-insert/db-wal-recovery, all `best=0 niches=1`) that will re-park —
>   i.e. the trainer box was tied up on provably-null work. **`moe_orchestrate.sh`** now waits for BOTH
>   (a) the .20 lanes' in-flight jobs to finish (nothing lost; auto-park records fresh 2.1 evidence) and
>   (b) the MoE collect to finish growing the corpus, THEN fires **`moe_train_eval.sh`**:
>   harvest (CANONICAL `compass harvest-tbench` — my raw MoE trajs feed it directly; `verified is False`
>   check passes `None` through) → **canary leakage gate** (MoE traces verified canary-CLEAN) → stop the
>   two .20 workers **by tmux session** (never `pkill -f` — the self-kill land-mine) → stop .20's
>   llama.cpp server → QLoRA on .20 (the certified trainer: one 48GB card holds the 4-bit 31B, so
>   **seq-1024** vs .22's 512-OOM) → LoRA→gguf → `trap restore EXIT` restarts .20's server, workers return
>   on the supervisor's next tick. **Supervisor seam:** `moe_train_eval.sh` added to
>   `experiment_active()` so collection is DEFERRED (else the */10 cron would relaunch the very workers we
>   stopped and contend for the freed GPU). **Guard verified live:** `MIN_TRAJ=12` aborted cleanly at
>   corpus=6 *before* touching any worker or server. Vendored to `research/selfdistill/`.
> - **Side finding → FIXED (drydock):** the 31B ITSELF loops — caught `caffe-cifar-10` repeating
>   `get_cifar10.sh`, "no output for 571s" (the 13h lane stall). Root cause: a command hung SILENTLY on a
>   long timeout, below the total-runtime auto-bg cap. Fix `88ca659`: config `bash_idle_bg_secs` — a
>   command silent for N s is adopted as a background job (non-destructive; agent freed + nudged). Also
>   fixed a latent `read(8192)`-blocks bug (→ `readline`). Default off; **fleet deploy = publish + bump
>   DD_VER + set `bash_idle_bg_secs=300`**. 33 bash/jobs tests pass.
> - **Reversible:** .22 MoE = container `moe-smoke`; `docker start llamacpp-gemma4-31b` restores the 31B.
>   Campaign lanes on .20/.21 untouched throughout. Smoke harness: `moe_smoke/`, `moe_smoke_solve.sh`.

> **🔀 2026-08-19 — CAMPAIGN SUBSTRATE SWITCHED TO TERMINAL-BENCH 2.1 (drop-in; honest baseline reset 60→48).**
> Upstream shipped tbench **2.1**, a more-verified iteration of 2.0: SAME 89 task names (drop-in,
> directly comparable), fixes **28/89** tasks — 9 external-dependency, 8 resource/budget, 11
> misspecification — plus reward-hack hardening. Decisive for the mission: **"no task is unsolved in
> 2.1"** ⇒ an honest 100% is *attainable* on 2.1 but was NOT on 2.0 (some tasks were unsolvable-by-bug,
> which is exactly where our fleet's flatlines were burning eratchet cycles). Still terminal-bench-2, so
> consistent with the tbench-2-only directive. **All migration steps reversible — snapshots in
> `frontier/selfdistill/heredity/migration-2.1-2026-08-19/`:**
> - Vendored 2.1 from `harbor-framework/terminal-bench-2-1@main` (`7131e43`) into the canonical
>   `tasks/terminal-bench-2/` (2.0 archived to `tasks/terminal-bench-2.0-snapshot-2026-08-19/`). Same
>   PATH ⇒ every fleet script unchanged (they all hardcode `tasks/terminal-bench-2`). `task.toml`
>   schema 1.0→1.1; one rename `install-windows-3.11`→`install-windows-3-11`. Directory rename is safe
>   for in-flight containers (their mounts follow the inode → they finish their 2.0 task; new claims read 2.1).
> - **Invalidated 13 changed-AND-solved markers** (adaptive-rejection-sampler, build-pmars, caffe-cifar-10,
>   configure-git-webserver, extract-moves-from-video, filter-js-from-html, fix-git, hf-model-inference,
>   mteb-leaderboard, mteb-retrieve, sam-cell-seg, sanitize-git-repo, torch-tensor-parallelism) → they
>   re-verify on 2.1 (moved to `ratchet/invalidated-2.0-2026-08-19/`). Unchanged solves kept (comparable).
> - **Unparked all 11 flatlines** + reset `ratchet/ratchet_evolve_results.csv` (2.0 rows archived) so the
>   flatline auto-park only fires on FRESH 2.1 evidence — the external-dep/resource/misspec fixes may
>   unblock several (make-doom-for-mips, make-mips-interpreter, db-wal-recovery, regex-chess, feal-*,
>   dna-*, gpt2-codegolf, chess-best-move, break-filter-js-from-html) that were unsolvable-by-bug on 2.0.
> - **Honest 2.1 baseline = 48/89** (2.0 was 60; the 13 re-verify + reset — real solves reconfirm in ~1
>   round, and 2.1-fixed tasks now become newly winnable). Scoreboard/effort/park all keyed by the
>   unchanged task names. Upstream provenance: [tbench.ai/news/terminal-bench-2-1]; the OLD vendored 2.0
>   came from the private `fbobe321/RSI.git` (token-in-remote — still needs rotating).

> **🔒 2026-08-18 — BACKGROUND-JOB COMPLETION-NOTIFY REMOVED ENTIRELY (no notify surface, no phone-home).**
> A completion-notify hook was briefly added (run an operator command when a background job finishes),
> first as a `config.toml` key then reworked to an env-only var. Operator decision: **remove it outright,
> "just to be sure"** — the shipped tool now has NO way to run any extra command on job completion.
> `launch_background` takes no notify param; no `DRYDOCK_JOB_NOTIFY_CMD` env is read; the `job_notify_cmd`
> config key is gone. The background-jobs feature itself stays (detached run + `Jobs` tool for status);
> the agent reports completion by checking the Jobs tool, never by pushing. Guard test
> `test_no_completion_notify_surface` proves the param is absent and the dead env var fires nothing.
> The wheel contains zero Telegram/network-send code (verified: only local-model httpx/urllib). Fleet-side
> completion pushes live entirely in the operator's own `research/selfdistill/tg_notify_*.sh` scripts,
> which are NOT part of the pip package.

> **📊 2026-08-16 — CAMPAIGN 55/89; supervisor + Comms shipped; drydock 3.1.16 YANKED.**
> tbench-2 **55/89 (62%)** (from 38 on 08-14), 235 traces. **Eratchet is cracking the hard tail** —
> evolved-ratchet solves incl. `fix-ocaml-gc` + `mteb-leaderboard` (both former held-out, base 0/30).
> Effort tiers: 18@t1 27@t2 5@t3.
> - **`fleet_supervisor.sh`** (cron */10 + @reboot, flock) — never-idle self-healer: worker keepalive,
>   per-task effort escalation ratchet→eratchet (`tbench_effort.tsv`), zombie reaper, experiment-queue,
>   unified `tasks/pool` + `gen_pool.sh` generator. **Fixed a flock-FD leak** — FD 8 inherited by spawned
>   workers locked the supervisor out for hours; workers now launch with `exec 8>&-`.
> - **Daily Telegram report** (`campaign_daily_report.py`, 06:35): solved/89, 24h delta, health.
> - **Drydock Comms Phase-1** (`drydock/comms/`): event bus → DETERMINISTIC attention policy → channels;
>   non-suppressible approval/blocking/security invariants; testable silence (10 tests). Dogfooded via
>   `comms_alert.sh` (agent.stuck → Telegram when a task maxes out).
>   **⚠️ INCIDENT:** 3.1.16 shipped a Telegram sender (reads cred + POSTs api.telegram.org) IN THE CORE,
>   past the HIGH security gate — the phone-home shape that quarantined v2. Remediated: sender removed from
>   core (pluggable adapter injected at the operator site), clean **3.1.17** shipped, **3.1.16 YANKED**.
>   Process fix: release via `scripts/release.sh` so the HIGH gate is honored (don't pipe build→scan→upload).
> - Versions: **drydock-cli 3.1.17** · **omniterm 0.1.92** (scroll-back + last-letter fixes). Full spec:
>   README_RUN.md PROGRESS block. See [[feedback_loop_detection]].
>
> **🎯 2026-08-14 — NORTH-STAR: SOLVE terminal-bench-2 COMPLETELY (ratchet+eratchet+self-distill), then next benchmark.**
> Operator: "solve tbench, don't care how long, with self-distillation; ratchet and eratchet get us there." Fleet
> switched from MEASUREMENT to an unattended **completion campaign**. Progress **37–38/89** (`tbench_progress.sh`).
> - **Escalating effort per task** (`fleet_supervisor.sh:seed_tbench_escalation`): each cycle an unsolved task
>   survives it climbs tier0 ratchet×8 → tier1 ratchet×12 → tier2 eratchet×12 → tier3+ eratchet×16 (state in
>   `heredity/tbench_effort.tsv`). Added an **eratchet job type** to `cluster/worker.sh`. All 52 unsolved queued.
> - **Never-idle supervisor** now drives the campaign: keeps workers alive, re-seeds+escalates the residue, reaps
>   zombies, drains an **experiment_queue**, and when tbench-2 = 89/89 auto-advances to the next benchmark (unified
>   `tasks/pool` + `gen_pool.sh` continuous generator). Cron */10 + @reboot; halt via `cluster/STOP_FLEET`.
> - **Self-distillation write-back** layers on: solves feed training; promote a champion only if it COLLECTS MORE
>   (compounding), else base grinds. **LAND-MINE hit + logged:** `pkill -f <pat>` where `<pat>` is in your own
>   cmdline self-kills (exit 144) — kill by PID/PGID. Also fixed `_queued` (was matching old done/failed rows →
>   previously-attempted tasks never re-tried). Full spec: README_RUN.md NORTH-STAR block.
>
> **🔬📉 2026-08-14 — REFRAME: held-out transfer → WITHIN-LOOP COMPOUNDING (operator: "199 is tiny").**
> **Totals:** 38 distinct tasks solved · **199 traces** (37 canonical + 162 variants) · 141 SFT rows; champion=base.
> **Held-out verdict = NULL but INSTRUMENT-LIMITED:** EVAL_N=5 re-measure → base **0/30 = adapter 0/30** (CIs
> overlap; the curve's +1/+2 was noise). Multi-round probe on the frozen held-out set → base **0/30 EVEN
> multi-round** ⇒ the set is beyond base's frontier, so the instrument floors everyone and measures nothing.
> **⇒ Two levers now** (vs waiting for a generalization-scale corpus we can't collect at ~1–2 solves/day, pool drained):
> (1) **within-loop compounding** — `compound_measure.sh` on **.22** (HF-Q5): base vs base+adapter over 10 UNSOLVED
> frontier near-misses, metric = graded checks-passing **FITNESS** (dynamic range), leakage-clean. *Does inheriting
> the 199 solves make more frontier progress than base?* (2) **pool expansion** — `ml_suite_sweep.sh` on **.20/.21**:
> collect the FRESH **ml-suite (13 ML tasks)** → grows + DIVERSIFIES the 1-species corpus. **Infra:** drydock **3.1.15**
> deployed + fleet DD_VER unified (edit-thrash breaker, compaction, /loop fixes). `heredity_loop` stays PAUSED
> (STOP_HEREDITY) until the instrument is fixed. New: compound_measure.sh, ml_suite_sweep.sh, probe_mr.sh,
> probe_band.sh, heredity/frontier_pool.txt, heredity/leaveout_candidates.txt. Full spec: README_RUN.md STATUS block.
> See [[project_two_box_ratchet_infra]], [[project_ratchet_heredity_reframe]].
>
> **🖥️🧬 2026-08-09 — THIRD BOX (.20 RTX 8000) STOOD UP AS TRAINER+COLLECTOR; HEREDITY v2 = STILL NULL at 2× corpus.**
> **New machine `192.168.50.20` (hostname `Zeus20`), Quadro RTX 8000, 48GB, single card, 8 CPUs, 93GB RAM.**
> Operator-provisioned bare; brought fully online this session (keyless SSH `bobef@remus` key; sudo=`lis4351`).
> Its `/data3` is a SEPARATE local disk (~246GB free), not shared with `.22`. Installed docker 29.7.2 +
> nvidia-container-toolkit 1.19.1 (GPU-in-docker verified); copied the QAT gguf + mmproj + the 59GB HF base +
> the `drydock` conda env + compass `.venv-train` (to identical paths so the venv symlinks resolve).
> - **COLLECTOR (LIVE):** `ratchet_20_sweep.sh` runs on the `.22` harness host (ddt containers there) driving
>   `.20`'s llama.cpp server (`LLM_URL=http://192.168.50.20:8000/v1`, single card **no --tensor-split**,
>   `-c 65536 -np 2`, `DD_VER=3.0.138`). The 73-task pool is FULLY attempted by `.21`+`.22`, so `.20` does
>   **ESCALATION** instead: the 35 closest never-solved near-miss partials (video-processing 4/5, path-tracing
>   4/5, overfull-hbox 3/4 …), ranked by closeness, at **MAX_ROUNDS=10** (vs `.21`/`.22`'s 5) — betting they
>   were round-capped, not flatlined. Own results `ratchet20/ratchet20_results.csv`; excludes solved-anywhere;
>   `ddt_<task>`-exists guard prevents cross-stream collisions. Any crack = a NEW corpus trace.
> - **TRAINER (CERTIFIED):** RTX 8000 = Turing sm_75 — NO bf16 (train fp16), no FA2 (SDPA); torch 2.11+cu128 +
>   bnb 0.49.2 4-bit DO run on it (verified: fp16 matmul + a real QLoRA smoke on the 31B, **loss 6.84→5.74**,
>   adapter saved). The 48GB card holds the whole 4-bit 31B on ONE GPU → **no `device_map=auto` naive-pipeline
>   waste** (on `.22` that split idles GPU0 at 0% while GPU1 does 100% — seen live), and enables seq-1024/bigger
>   batch. Steady-state = time-share: `.20` TRAINS when a job is queued (server stopped), else SERVES/collects.
>   This breaks the serve-vs-train mutual-exclusion → `.21`+`.22` can collect continuously while `.20` trains.
> - **SPEED (measured, all boxes under sweep load, 200-tok generation-bound):** RTX 8000 single **19.2 tok/s** vs
>   `.22` 2×4060Ti tensor-split **14.3** vs `.21` vLLM **13.5** → **~1.34×** the tensor-split under load, **~1.9×**
>   idle (`.20` hit ~28 tok/s idle vs the ~15 tensor-split baseline). Modest for single-stream; the real value is
>   TRAINING (no pipeline waste) + a 3rd independent lane.
> - **🧪 HEREDITY v2 VERDICT: STILL NULL.** Rebuilt the corpus from 31 fresh ratchet solves (31 DISTINCT tasks —
>   COBOL/ELF/stan/arc-agi/git/…, vs v1's 16), condensed 31/31 → 31-row SFT (53 windows). QLoRA on matched HF base,
>   120 steps, loss 5.24→**0.009** (fully memorized). Frozen eval: **ho=0/6 (base 0), reg 0/4 (base 1) → transfer=0,
>   identical to v1**, same mild regression. Growing the corpus 2× AND diversifying to 31 distinct tasks did NOT
>   crack the wall. Key tell: `speciate.py` clusters all 31 into **1 species** — semantically diverse tasks, uniform
>   condensed FORMAT → the model memorizes format→solution, not skill. **⇒ next lever is NOT more data:** (a) speciate
>   on the raw INSTRUCTION → one adapter PER species; (b) change the training TARGET toward reasoning/skill; or
>   (c) accept self-distill-on-solves doesn't generalize and pivot. Now that `.20` is a certified independent trainer,
>   (a)/(b) can run as parallel training experiments WITHOUT stalling collection. Files: `heredity/heredity_sft.jsonl`
>   (v2 31 rows; v1 backed up `heredity_sft.v1-16.jsonl.bak`), `heredity/heredity_eval.csv` (both rows),
>   `Models/heredity-lora.gguf` (v2, 234M), `ratchet_20_sweep.sh`. See [[project_two_box_ratchet_infra]],
>   [[project_ratchet_heredity_reframe]].
>
> **🕸️ CLUSTER SCHEDULER built (operator: "3 boxes as a cluster").** MEASURED the interconnect first:
> all 3 boxes are on **WiFi** (`.22`=wlp2s0, `.20`=wlp4s0, `.21`=USB wlx dongle), **~112 Mbps**
> (`.22`↔`.20` 14 MB/s) with SEPARATE local `/data3`. ⇒ tightly-coupled distributed training
> (DDP/FSDP, GB of gradient sync/step) is OUT; the right model is a **task-parallel cluster**: one
> shared queue on `.22`, workers drain it, only tiny job/result rows cross the network. Built in
> `frontier/selfdistill/cluster/`: `lib.sh` (flock-atomic `q_enqueue`/`q_claim`/`q_complete` over
> `queue.tsv`), `worker.sh <id> <server_url> [dd_ver]` (claim→run `ratchet_solve` locally→result;
> `ddt_<task>`-exists collision guard; STOP-flag), `enqueue.sh` (`escalation` seeds unsolved
> near-miss partials closest-first), `status.sh`, `README.md`. **`.20` CUT OVER to the scheduler**
> (worker `20a` in tmux, 34-job escalation queue) replacing the manual `ratchet_20_sweep`; verified
> claiming + driving `.20`. `.21`/`.22` still on their manual sweep/training (migrate when free).
> **TWO LAND-MINES for the next session:** (1) launch workers in **tmux** — `ratchet_solve` drives
> `tmux send-keys`, so a bare `setsid`/`nohup` worker dies (signal 16); (2) NEVER `pkill -f <pat>`
> where `<pat>` appears in your OWN command — pkill matches the launching shell's cmdline and kills
> it (exit 144, silent). Kill by tmux session / PID. **WIRING to GbE = SLATED FUTURE WORK** (operator
> decision) — only needed for multi-node training, not task-parallel. **Phase 2:** deploy the harness
> to `.20`/`.21` so their ddt containers run LOCALLY (today all containers run on `.22`'s 4 CPUs = the
> concurrency cap).

> **🧪 2026-08-07 — HEREDITY VERDICT: NO GENERALIZATION (the mission experiment answered, negative & clean).**
> Trained a LoRA on the 16-trace heredity corpus (matched HF base, 60 steps, loss 4.68→**0.016** = fully
> memorized) → converted → served MATCHED HF-Q5 base+LoRA → eval on the FROZEN held-out + regression.
> **Result: held-out 0/6 (base 0/6) → ZERO transfer; regression 0/4 (base 1/4) → LOST configure-git-webserver
> (1→0).** Gate correctly refuses to promote → champion stays base. So at **2× the corpus** that stalled the
> original run (16 vs 7 traces), self-distillation-on-solves STILL doesn't generalize and mildly regresses —
> this is the standing generalization wall, now confirmed at the mission's own experiment. Caveat: n=1, EVAL_N=1
> (the reg 1→0 could be TUI-drive noise), corpus speciated to 1 cluster (too homogeneous — the diversity lever
> matters). Artifacts: `heredity/heredity_eval.csv`, `Models/heredity-lora.gguf`. **Next candidates:** (a) grow a
> MORE DIVERSE corpus (the 2-stream ratchet below is now collecting it) before retrying at larger scale;
> (b) the finer-fitness lever to convert more flat-aborts into solves → more/varied traces.
> **Both boxes now BUSY on the finer-fitness ratchet (2 streams, separate GPUs):** `.21`/vLLM =
> `ratchet_21_sweep.sh` (top-down, DD_VER=3.1.8), `.22`/llama.cpp = `ratchet_22_sweep.sh` (reverse order,
> DD_VER=3.0.138), 52-55 candidates each, converging from opposite ends with collision guards; own result files
> (`ratchet21/`, `ratchet22/`).
> **RESULTS through 2026-08-08 ~10:50 (30 tasks attempted, 8 SOLVED, both streams still running):**
> - `.21` (18 done, 6 solved): build-pmars 4/4 · cobol-modernization 3/3 · constraints-scheduling 3/3 ·
>   distribution-search 4/4 · extract-elf 2/2 · git-multibranch 1/1.
> - `.22` (12 done, 2 solved): sqlite-with-gcov 3/3 · rstan-to-pystan 6/6.
> - **13 partial=660 stepping-stones the OLD coarse fitness would have discarded/flat-aborted** — several
>   very close: video-processing 4/5 · winning-avg-corewars 2/3 · sanitize-git-repo 2/3 · caffe-cifar-10 3/6 ·
>   extract-moves-from-video 1/2 · gcode-to-text 1/2 · train-fasttext 1/2. This is the finer-fitness lever
>   earning its keep: it preserves "ran-but-wrong" states instead of restarting from a crash.
> - **Corpus payoff:** 22 harvestable `*_solved_traj.json`, now spanning many DISTINCT domains (COBOL, SQL/gcov,
>   R↔Python, constraint-scheduling, ELF, git, corewars) — directly attacks the heredity failure's root cause
>   (16 traces → 1 cluster, too homogeneous). Sets up a **heredity retry v2** on the enlarged/diversified corpus.
> See [[project_two_box_ratchet_infra]].

> **📈 2026-08-07 — 2ND LEVER SHIPPED: finer-grained ratchet fitness (sub-goal gradient, no more plateau-cliff).**
> The plain ratchet's fitness was `passed/tests` from the CTRF summary — but most tbench tasks ship a SINGLE
> monolithic test, so `passed` is binary (0/1) and the pawl had no intermediate gradient (and `ps>best` strict-
> greater discarded every stepping-stone). Fix: extract a sub-goal signal already latent in the CTRF per-test
> `trace`/`raw_status` — did the solution RUN TO AN ASSERTION (computed a wrong answer — close) or CRASH EARLY
> (ImportError / missing artifact — far)? Three ordinal levels per failing test: soft assertion 0.66 · called-but-
> crashed 0.33 · never-ran 0.0. Fitness is now lexicographic **(passed, partial)**; the pawl locks in a strict
> pass increase OR a within-plateau partial gain (crash→runs-but-wrong), and early-abort is stall-based (no lex
> gain for N rounds) so a task still climbing partial isn't killed. **Files:** `ctrf_fitness.py` (reusable parser,
> stdin CTRF → `passed tests partial_milli`), `ratchet_solve.sh` (lex pawl, gated by `FINE_FITNESS=1` default —
> set 0 to restore the strict pass-count pawl for a controlled A/B), `test_ctrf_fitness.py` (validation: synthetic
> levels + real feal/overfull samples, ALL PASS). CSV gains a `partial` column (3 plain-ratchet runner headers
> updated; eratchet left 4-col). **LIVE on .21:** `ratchet_21_sweep.sh` now runs the finer-fitness ratchet
> (`FINE_FITNESS=1`, `DD_VER=3.1.8` for the vLLM skip_special_tokens fix, `LLM_URL=.21`) over 55 unsolved
> non-held-out pool tasks — keeps the otherwise-idle .21 box busy while .22 runs heredity (separate GPUs, no
> contention). Non-disruptive: own results file `ratchet21/ratchet21_results.csv`, kills nothing, excludes
> heredity's held-out/regression tasks. Verified .21 taking load (`num_requests_running=1`). Many of the 55 are
> prior flat-aborts (break-filter-js, overfull-hbox, video-processing, feal-*, caffe) — a natural test of whether
> the partial gradient converts them into cracks. A clean `FINE_FITNESS=1` vs `0` A/B still worth running once a
> box frees. See [[project_two_box_ratchet_infra]].

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

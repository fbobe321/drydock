# PRD — The Internalized Ratchet: fine-tuning the search loop into the model

**Status:** DRAFT / design (2026-08-18); **updated 2026-08-23** with two measured results that
bear directly on the plan — see **§12**. No weight-training run authorized yet — this doc
defines the target, the data, and the honest eval *before* we spend compute.
**Owner:** self-distill campaign (`/data3/tbench_local/frontier/selfdistill/`).
**One-line thesis:** ratchet/eratchet is inference-time scaffolding that lives in
the *harness*; train that control loop into the *weights* so the model runs it on
its own — the way chain-of-thought moved from a prompt trick to a learned behavior.

---

## 1. Motivation — the CoT precedent

Early models didn't reason step-by-step; people *prompted* it ("let's think step by
step"). CoT was external scaffolding. Then we fine-tuned models to produce the
reasoning **on their own**, and they got better for it. The scaffolding became a
capability.

Ratchet/eratchet is in the same place CoT was: a **harness-level** process —
cumulative selection (the pawl), diverse parallel exploration, escalation-on-stall,
rollback-on-regression — that sits *outside* the model. The bet of this program is
that the same external→internal move applies: distill the loop into the policy so
the model self-corrects within a single context, becoming its own harness.

Precedent this is NOT novel-in-a-vacuum (so we can borrow method, not reinvent):
STaR, ReST, rejection-sampling fine-tuning (RFT); reasoning-model training on search
traces; AlphaZero distilling MCTS into the network. Our ratchet is the *teacher /
search*; the model should learn to *be* it.

## 2. Hypothesis (falsifiable)

> Training a base model on ratchet/eratchet trajectories reformatted as **single-context
> self-correction** — including the failed attempts and the recoveries — plus the
> verifier as a **reward**, over a **large, diverse, verifiable task pool**, will raise
> its **held-out, single-shot, harness-OFF** solve rate above the base model's.

If the held-out single-shot number does **not** move (with a non-floored eval and
adequate task diversity), the internalization bet is not paying and we stop — see §9.

## 3. The distillation target — the LOOP, not the answer (the load-bearing decision)

CoT internalization worked because we trained on the reasoning **steps**, not final
answers. The exact analogue here:

- **WRONG (what a naive run does, and roughly what our one null attempt did):** SFT on
  the *winning final trajectory*. This clones the *product* — memorized solutions to
  the 56 solved tasks. It cannot transfer, and it teaches nothing about *how to search*.
- **RIGHT:** train on trajectories that contain the **failure→feedback→adaptation→retry**
  structure, so the model learns the *policy*: when a verifier says 3/8, keep what
  passed, diagnose the failing checks, DON'T repeat the stalled move, try a different
  strategy, escalate when stuck, converge. We compress a multi-round ratchet run into
  one self-correcting context. The harness's rounds become the model's inner loop.

The unit of supervision is a **control decision**, not a solution.

## 4. Data format — the self-correction trajectory

Each ratchet/eratchet run becomes one (or a few) training trajectory of the shape:

```
SYSTEM/TASK:   <goal + environment, tests NOT present (test-blind, see §8)>
ATTEMPT 1:     <the model's work>
SIGNAL 1:      <verifier feedback: k/N checks pass, WHICH ones fail>   ← the gradient
REFLECT 1:     <diagnosis + explicit decision to change approach>      ← the taught step
ATTEMPT 2:     <a DIFFERENT strategy, keeping what passed>
SIGNAL 2:      k'/N ...
...
ATTEMPT n:     <solved>
```

Sources we already produce, and how they map:
- **Round transcripts** (`ratchet/*.evolve.log`, per-round drydock traces) → the
  ATTEMPT/SIGNAL pairs. The pawl/rollback record tells us which rounds improved.
- **VariationPolicy operator** (exploit→diversify→restart) → the REFLECT step's content
  (the harness already decided "stall → change strategy"; we verbalize that decision so
  the model learns to make it).
- **eratchet archive** → **contrastive** trajectories: from the same base, a 5/8 and a
  3/8 variant. These are preference/advantage pairs (§5), not just positives.
- **Reward** attached per trajectory = final passed/total (and per-step gradient).

Deliverable of the *reformatter* (the no-regrets foundation, built after this doc):
`selfdistill/distill/reformat.py` → emits JSONL of `{messages, reward, meta}`.

Open format questions: (a) how much of the failed attempt to keep vs summarize (token
budget vs signal); (b) verbalizing REFLECT — synthesized post-hoc vs harvested from the
model's own between-round reasoning; (c) whether to teach rollback explicitly.

## 5. Training approach — phased, cheapest-first

- **v0 — Reward-weighted / rejection-sampling FT (STaR/ReST/RFT).** No RL infra; reuse
  the existing finetune-bench pipeline. Keep trajectories whose reward clears a bar;
  weight by advantage. Cheapest test of whether the *target/format* teaches anything.
- **v1 — Preference / advantage (DPO-style or GRPO).** Use eratchet's contrastive pairs
  (better-vs-worse from the same base) directly. This is where the verifier-as-reward
  earns its keep; more sample-efficient for policy learning than SFT.
- **v2 — On-policy RL (GRPO/PPO) with the verifier as reward**, only if v0/v1 show signal
  and the task pool is large enough to not overfit.

Start at v0. Do **not** build RL infra before v0 shows the format moves a held-out number.

## 6. The eval — the honest instrument (build alongside the reformatter)

The metric must measure **internalization, not memorization**:

- **Held-out task split.** Tasks NOT used to produce any training trajectory (and
  deduped against them — §8). The training tasks' score is meaningless (it's the
  training set).
- **Single-shot, harness OFF.** No ratchet, no multi-round, no verifier-in-the-loop at
  inference. One attempt. Did the model self-correct *within its own context*?
- **Behavioral probe.** Independent of pass/fail: does the model, unprompted, exhibit
  the loop — read feedback, change approach after a stall — inside one context? (An LLM
  judge on the trace, or markers.) This catches partial internalization the binary
  solve-rate misses.
- **Non-floored.** The prior null was `base 0/30` — the eval floored everyone, so no
  method could show lift. The held-out set MUST contain tasks the base model solves
  single-shot >0% and <100% (a measurable band). Curate for gradient.

Primary number: **held-out single-shot solve rate, trained vs base.** Secondary:
behavioral-loop rate; sample efficiency; and a no-regression check on general ability.

**Latency is a first-class metric (operator, 2026-08-18).** "Ratchet takes a while."
Internalizing the loop should make the model self-correct in FEWER external rounds — so
measure **rounds-to-solve** and **wall-clock-to-solve**, base vs tuned, at equal accuracy.
A tuned model that solves the same tasks in fewer harness rounds is a win even before the
accuracy needle moves. (Harness-level latency — parallelism, cheaper verifies, early-exit —
is a separate, complementary lever.)

## 7. Task diversity / scale — the real blocker (method is not the risk)

56–89 tasks cannot teach a *general* loop; the policy would overfit to these tasks'
idioms. The self-distillation campaign's real job is **generating a large, verifiable,
diverse task pool** (the "199 is tiny" instinct, formalized). Requirements:

- **Verifiable** — every generated task ships a checker (so it can be a ratchet fitness
  signal AND an eval item).
- **Diverse** — across domains/skills, not near-dupes, so the learned loop generalizes.
- **Scale target (straw man):** ≥ ~1–2k tasks for a training pool that could plausibly
  generalize; held-out ≥ ~200 with a measurable base-solve band. (Refine with a
  learning-curve pilot: solve-rate lift vs pool size.)
- Reuse: `gen_pool.sh` / the task generator already in the campaign; harden the
  verifiable-generation + dedup path.

## 8. Integrity — no leakage, no test-to-train contamination

- **Test-blind traces.** The campaign already deletes `/tests`+`/logs` before the
  snapshot the model sees (anti-cheat in `ratchet_solve.sh`/`ratchet_evolve.sh`); training
  trajectories inherit this — the model never sees the checker it's graded on.
- **Train/eval separation.** Held-out tasks and their checkers must never appear in any
  training trajectory; run the **contamination scanner** (already in the pipeline) over
  the reformatted corpus vs the eval set.
- **No reward hacking into the corpus.** A trajectory that "passed" by tampering with the
  environment must be filtered (re-verify with a fresh checker before it enters training).

## 9. Risks & kill criteria (decision gates, not vibes)

- **The one data point:** our prior SFT transfer was null. Read: *inconclusive* (floored
  eval + wrong target + too few tasks), NOT a refutation — but it sets the bar. If, with
  a non-floored held-out eval and an adequate pool, **v0 shows no held-out lift**, the
  internalization bet is not paying → stop before RL infra.
- **Base-capability ceiling.** If the base model lacks the latent skill to be coaxed, no
  distillation helps — the behavioral probe + base single-shot band tell us early.
- **Overfit signal:** training-task score climbs while held-out is flat → memorization;
  needs more task diversity, not more steps.
- **Cost gate:** each phase (reformat → v0 → eval) is a go/no-go; no phase starts before
  the prior one's number is in.

## 10. Milestones

1. **M0 (this doc):** target, data format, eval defined. ✅ draft
2. **M1:** reformatter (`distill/reformat.py`) → self-correction JSONL + reward. ✅ **DONE
   (2026-08-18).** Over 60 solves: **53 contain the self-correction loop** (median 3
   spans; 266 total), 7 one-shots, **leaked = 0** (test-blind). Key finding: *self-testing
   ≠ leakage* — the model running its own tests / heeding the verify nudge is the loop we
   want (counted as `n_self_verifies`), not contamination; the first-pass scan's 18 flags
   were all false positives, now fixed to grader-body-only. Corpus: `distill/corpus.jsonl`;
   10 unit tests green. Limitation: reward is 1.0 for all (solves); the partial-credit
   gradient + eratchet contrastive pairs (§5) await the per-variant payloads.
2b. **Full-fidelity capture:** ✅ **DONE (drydock 3.1.22).** `drydock eratchet --capture
   <file>` now records EVERY variant (not just the winner) with its transcript + reward +
   the shared base_ref/generation → `contrastive_pairs()` extracts (chosen, rejected)
   preference pairs (a 5/8 next to a 3/8 from the same base). This is the RL/preference
   signal M1's winner-only corpus lacked — harvested by *re-running* the campaign through
   `drydock eratchet` (operator sanctioned re-runs, §11b). Run from the TASK repo.
3. **M2:** held-out single-shot eval harness (ratchet OFF) + curate a non-floored split.
   ✅ protocol + latency (`distill/eval_harness.py`, 7 tests). TODO: the real ddt/worktree
   single-shot runner + establish the base baseline on a curated non-floored split.
4. **M3:** v0 reward-weighted FT on a *pilot* pool; report held-out lift vs base. **GO/NO-GO.**
5. **M4 (if GO):** scale the task pool (§7) + v1 preference training.
6. **M5:** on-policy RL, only if the curve keeps paying.

## 11b. The flywheel — this is not a one-shot (operator, 2026-08-18)

Solving tbench-2 completely is a *milestone, not the goal*. The real loop:

```
ratchet/eratchet harness  ──solves──▶  traces (self-correction loops)
        ▲                                        │
        │                                   distill (SFT/RL)
   put tuned model                               ▼
   BACK in the harness  ◀──ratchet-native──  model that self-corrects on its own
```

A model tuned on ratchet-thinking, dropped back into the harness, should (a) solve more,
and (b) solve in **fewer rounds** (faster — the latency win). Then it generates *better*
traces, which distill into a *better* model. Each turn of the flywheel raises the floor.
This is the AlphaZero shape (self-play → stronger net → better self-play), with a verifier
as the reward and the ratchet as the search.

**Benchmark-agnostic by construction.** tbench-2 is the first proving ground, NOT the
target. Everything — reformat, eval, training, the task-gen pool — must be verifier-driven
and benchmark-neutral so the same pipeline points at the next benchmark (and eventually
"all of it") by swapping the task set + checker. No tbench-specific assumptions in the core.

**Re-runs are sanctioned (operator).** We do NOT have to make do with the lossy existing
corpus. It is fine to re-instrument the ratchet/eratchet to persist FULL fidelity (every
variant attempt's transcript + per-step score + the contrastive pairs) and re-run to
harvest it. Capturing the data we need > mining what happened to survive.

## 12. 2026-08-23 — measured results that update this plan

Two numbers landed today, both prerequisites this PRD leans on.

### 12.1 What the harness search actually buys — the internalization TARGET, quantified
The honest single-attempt baseline (no ratchet, no score feedback, harness-minimal) =
**25/88 = 28.4%** (Wilson 95% CI [20.0, 38.6]); the verifier-guided campaign = **63/89 =
70.8%**. Test-time search buys **+42.4 pts (2.49×)** on Gemma-4-31B / TB-2.1. This is exactly
the quantity §2's hypothesis wants to fold into weights: internalization is "worth it" to the
degree a **harness-OFF** tuned model recaptures part of that +42 without the external loop. It
sharpens the kill criterion (§9): M3's held-out lift is now graded against a KNOWN **28.4% floor**
and a **70.8% ceiling** — the question is how far up that 42-pt gap the internalized loop climbs
single-shot. NOTE: the 70.8% is NOT leaderboard-comparable (score feedback + ~48 attempts/task);
only the 28.4% baseline is. Files: `baseline_sweep.sh`, `baseline_report.py`.

### 12.2 Can the model author its own fitness signal? — the off-benchmark unlock (§7 / §8 / §11b)
The whole PRD depends on a verifier: §7 needs every generated task to ship a checker; §11b demands
the pipeline be benchmark-agnostic. On a benchmark the checker is given; **off**-benchmark the
model must COMPILE the goal into an executable check. `selfcheck_probe.sh` measures whether it can,
leakage-free: author `/app/selfcheck.sh` from `instruction.md` alone (tests never mounted), freeze
it to the host BEFORE solving in a fresh container, then score the same solution with BOTH the
self-check and the official checker.
- **The headline is the FALSE-PASS rate** (self✓ official✗). For a SEARCH objective the two error
  types are asymmetric: a false-FAIL (over-strict check) only costs efficiency; a false-PASS
  (over-lax check) is **fatal** — search reward-hacks into confident wrongness. So the real
  question is not "is the self-check accurate" but "does it err in the SAFE (too-strict) direction."
- **Bug found + fixed (this session).** The sweep's `drive()` broke on loop iteration 1 (captured
  the TUI pane before its first sleep, matched a bare `ready`) → every task authored a 0-byte check
  and the experiment silently no-op'd (12/12 `NA`). Fixed to `control_noratchet.sh`'s proven wait
  logic (sleep-at-top + `⚓` glyph anchor); committed drydock-v3 `0d93379`.
- **First result:** `fix-git` → the agent authored a concrete 1076B check (branch + conflict
  markers + exact expected strings), solved the task, and self✓ MATCHED official✓ (2/2) — an
  agreement, with the check erring toward over-strict (the safe direction). Sweep now re-running the
  balanced 12 (4 true-pass / 4 near-miss / 4 flatline); confusion matrix pending.
- **Why this gates the flywheel:** low, safe-direction error ⇒ §7's "generate a verifiable task
  pool" and §11b's benchmark-agnostic claim get a concrete mechanism (the model writes the
  checkers). High false-pass ⇒ off-benchmark search is unsafe and the pool must keep human-approved
  checks (the fitness ladder in RESUME 2026-08-23 ③: compile-goal-to-check approved ONCE, quantitative
  self-scoring, differential/behavioural-equivalence, golden-example distance, property invariants).

### 12.3 Next steps (ordered)
1. Finish the selfcheck sweep → report the confusion matrix + false-pass rate (the headline).
2. If binary agreement holds in the safe direction, build the **GRADIENT** version: does a
   self-authored check produce a graded signal (dead→crashed→asserted→pass) that correlates with the
   official per-check gradient? The ratchet climbs a gradient, not a binary — this is the signal that
   actually matters for search, and the bridge from "self-check works" to "self-check can drive the ratchet."
3. Feed the verdict into §7: safe-direction self-checks ⇒ the task-pool generator emits checker +
   task together; otherwise pool-gen requires the cheap human-approve-one-check path.
4. M3 (v0 reward-weighted FT) remains gated on M2's real single-shot runner + a non-floored held-out
   split — unchanged by today's results.

## 13. 2026-08-24 — the META-RATCHET: ratchet the fitness signal itself

**Operator reframe:** the self-authored fitness signal "may be as important as solving a task —
describing a problem well is prerequisite to solving it — almost need a ratchet-type approach to
building good evaluation for the fitness signal." ⇒ a loop that sits UPSTREAM of the solution-ratchet:
*propose a check → score how good the check is → revise → repeat.* The check is the reusable asset;
the solve is disposable. §12.2's `selfcheck_probe.sh` measured a check ONCE; this makes the check a
first-class ratcheted artifact.

### 13.1 The crux — fitness-of-a-fitness-signal WITHOUT ground truth
Off-benchmark there is no official checker to grade a check, so the check's quality must be scored by
**discrimination / mutation**: a good check ACCEPTS a correct solution and REJECTS broken ones.
Specificity-under-mutation needs no ground truth (corrupt a solution → a faithful check must fail).
On-benchmark the official checker is demoted from "the thing we climb" to "the VALIDATOR that
mutation-specificity predicts real false-pass." Two axes:
- **specificity (GT-free) — MEASURED.** `selfcheck/check_specificity.sh` runs each authored check
  against an unsolved and an emptied `/app` (known-negatives a faithful check must reject).
  Result: **8/8 checks kill both mutants** — none are the trivially-degenerate `exit 0` type.
  Necessary, NOT sufficient: `fix-git`'s check aces specificity yet is path-fragile (greps bare
  `_includes/...` while the repo is at `/app/personal-site/`) → would false-FAIL a correct solve.
- **sensitivity / false-pass (needs passing + near-miss solutions) — the dangerous cell (self✓/true✗).**
  The old probe scored ONE (usually failing) solution, so this cell was near-empty and its
  "0% false-pass / 8/8 agree (7 both-fail, 1 both-pass)" headline was hollow.

### 13.2 `check_ratchet.sh` — populate false-pass, then ratchet the check
Author check (frozen) → **ratchet-solve capturing a docker snapshot at EVERY official-fitness
improvement** (`ckr_<task>:f<passed>`, tests deleted pre-commit = anti-cheat clean) = a LABELED
solution spectrum (true-pass + genuine near-misses) → score the frozen check against each snapshot
(this finally populates false-pass) → **META step:** if it false-passed any near-miss, tell the model
which requirement-count it wrongly accepted and have it tighten the check; re-score; record
check-fitness `v1→v2`. Outputs `check_ratchet/check_ratchet_{results,summary}.csv`.
- **Live seed (freed .20 lane):** `fix-git` authored 665B, solved 2/2 r1, self✓=true✓, fp=0
  (validates the pipeline; solved-immediately ⇒ no near-miss band). `video-processing` climbing
  2/5→3/5 (building the near-miss spectrum where a lax check would false-pass) — v1/v2 pending.

### 13.3 `graded_ratchet.sh` — apply it to the HARD (flatline) tasks
The hard tasks flatline because their official checker is a single monolithic test (`total=1`) ⇒
structurally binary ⇒ no gradient (RESUME 2026-08-22). `probe_fitness.sh`/`ctrf_fitness.py`
manufacture GENERIC gradient; the self-authored check is TASK-SPECIFIC — decomposing the spec into N
observable sub-requirements gives N gradient rungs the monolithic checker collapses into one bit. So
the model authors a **decomposed graded scorecard** (`SCORE=<passed>/<total>`) and the ratchet climbs
THAT. **Risk = reward-hacking** (a rich proxy is climbable but gameable), so the official checker never
leaves — demoted to an ORACLE scored every round but NEVER shown to the model. Verdicts:
`SOLVED-via-gradient` · `gradient-TRACKS` (authored↑ and official↑) · `PROXY-DIVERGED-rewardhack`
(authored↑ while official flat) · `stuck`. Built + registered; launches on the freed lane once the
`check_ratchet` seed validates (small-verified-steps: if the loop can't harden an EASY boolean check
it won't harden a hard graded one).

### 13.4 Next steps (supersedes 12.3)
1. Read `check_ratchet_summary.csv`: does `video-processing` (or a swapped-in richer near-miss like
   `sam-cell-seg` 8/9) show `false_pass_v2 < false_pass_v1`? That is the meta-ratchet's proof.
2. On validation, run `graded_ratchet.sh` on one hard flatline (`regex-chess` / `make-doom-for-mips`):
   does manufactured task-specific gradient produce cross-round movement where the binary signal sat
   at 0/1 — WITHOUT the proxy diverging from the oracle?
3. Feed the verdict into §7/§11b as in 12.2/12.3: safe-direction, oracle-tracking self-checks ⇒ the
   task-pool generator emits checker + task together; otherwise keep the human-approve-one-check path.

### 13.5 2026-08-26 — results in: self-authored checks err STRICT; graded signal cracks a flatline
Two threads reached honest conclusions.

**(A) Do self-authored checks ever false-pass? — NO, across every provocation (n=6).** This is the
trustworthiness question for using a self-authored check as a search objective. Results:
| provocation | tasks | false-pass |
|---|---|---|
| specificity: unsolved / emptied `/app` (`check_specificity.sh`) | 8 | 0/8 |
| honest near-miss (2/5–4/5, well-formed, fails ≥1 real requirement) (`check_ratchet.sh`) | 4 | 0 |
| adversarial decoy: truncate-to-empty + same-size garble (`check_decoy_probe.sh`) | 2 | 0/4 |
- **Conclusion:** on this distribution self-authored checks **err strict, not lax** — they verify actual
  content (reject emptied AND garbled outputs), catch a well-formed solution missing even one
  requirement, and never accept a wrong solution. For SEARCH this is the SAFE asymmetry: the residual
  risk is false-FAIL (wasted effort), never false-PASS (reward-hack into confident wrongness). It
  answers §12.2's headline: a self-authored fitness signal is trustworthy enough to climb here.
- **Honest limits (state plainly):** (1) the **revise loop (`v1→v2`) is built but EMPIRICALLY
  UNEXERCISED** — it never fired because no false-pass ever occurred; we can say it was not *needed* on
  6 tasks, NOT that it works. (2) The checks lean **over-strict** — we rarely confirmed a check ACCEPTS
  a truly-correct solution (only `fix-git`; whose check was even path-fragile), so false-fail is the
  live cost. (3) The one untested band is a **subtly-wrong VALUE** (valid format, wrong number);
  truncate/garble are too blunt to probe it, though the honest 4/5 near-miss is its closest real proxy
  and was rejected. (4) n=6, single model.

**(B) Can a self-authored GRADED signal manufacture gradient on a flatline without reward-hacking? —
first evidence YES (`graded_ratchet.sh`, `regex-chess`).** `regex-chess` was a pure campaign flatline
(26× `0/4`). Climbing its self-authored 7-point scorecard, the official ORACLE moved **`0/4 → 1/4`**
(round 2) — a real crack a binary signal never produced — and the run ended `gradient-TRACKS`: authored
rose 1→4/7 AND official rose 0→1, with the authored score NEVER running ahead of the oracle (no
`PROXY-DIVERGED`). So the manufactured gradient stayed honest. Limits: n=1, stalled at a PARTIAL (1/4,
not solved). **Now scaling** across a batch of hard flatlines (chess-best-move, polyglot-c-py,
make-doom-for-mips, dna-assembly) to get the distribution: how often does manufactured gradient crack a
flatline, and how often does the oracle catch divergence? That distribution — not the n=1 — is the
result worth publishing (the reward-signal / reward-hack-detection contribution, §12/RESUME 2026-08-24).

## 11. Open questions

- REFLECT step: harvest the model's own inter-round reasoning, or synthesize it? (Harvested
  is more honest; synthesized is cleaner but risks teaching a rationalization.)
- Do we teach rollback (undo a regression) explicitly, or only forward self-correction?
- One trajectory per run, or mine multiple (each improving prefix) for more supervision?
- Does internalizing the loop trade off against single-shot *speed* (the model now
  "thinks longer")? Acceptable if held-out accuracy rises — but measure it.

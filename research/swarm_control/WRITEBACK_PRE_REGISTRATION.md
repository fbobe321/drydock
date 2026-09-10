# Swarm as write-back fuel — does parallel candidate DIVERSITY improve self-distillation? (PRE-REGISTERED)

**Written 2026-09-09, before any data.** Predictions in `writeback_predictions.json`.
Companion to `PRE_REGISTRATION.md` (the solve-lift control). This is the direction the
programme's own prior *favors*: the ratchet's binding constraint is **write-back, not
search**, so the swarm's highest-value contribution is likely not a better single solve but
a **richer training corpus** — a population of diverse verified diffs per task, versus one
ratchet lineage.

## The hypothesis
A swarm of N parallel, diversity-injected Builders produces, per task, **several distinct
verified solutions** (different approaches that all pass the checker). Self-distilling the
base model on that *diverse* verified corpus lifts it more than distilling on the same number
of examples drawn from a single ratchet/eratchet lineage (which tends to converge on one
solution shape). More diverse correct demonstrations → better generalization.

## Two gated stages (do not run stage B until stage A clears)
**Stage A — the diversity gate (cheap, run first).** At **matched compute** (swarm and
eratchet within ±20% total tokens, same tasks/budget/server), measure **distinct verified
solutions per task**. `drydock.swarm.candidate_diversity(repo, candidates)` computes this:
verified candidates → normalized-diff fingerprints → distinct count + ratio. If the swarm is
**not** more diverse than eratchet at matched compute, the whole hypothesis is dead before any
GPU time — the swarm produces no richer corpus.

**Stage B — downstream lift (expensive, only if A clears).** Build two corpora of **equal
size** (same #examples): one from swarm-generated distinct verified diffs, one from
eratchet's. Self-distill the base model on each (existing selfdistill pipeline), eval on a
**held-out** tbench-2 split. The variable is corpus *diversity*, held at equal *quantity* —
so a lift is attributable to diversity, not to more data.

## Matched: compute for generation, SIZE for training
- Generation (stage A): equal total tokens (`run_swarm` metrics provide the swarm side;
  eratchet already logs its variant costs). A diversity win outside the ±20% band is a
  compute confound, discarded.
- Training (stage B): equal example count per corpus, so diversity is the only variable.

## Predictions (registered before data)
- **p1 — diversity gate.** On tasks both arms solve, the swarm yields **more distinct
  verified solutions per task** than eratchet at matched compute (target: swarm mean distinct
  ≥ 1.5× eratchet, or ≥ +1 distinct on ≥half the shared-solved tasks). *Falsifier:* swarm
  distinct ≤ eratchet at matched compute ⇒ the swarm produces no richer corpus ⇒ write-back-
  via-swarm is dead; do not run stage B.
- **p2 — downstream lift.** The swarm-corpus-distilled model solves **more held-out tasks**
  than the eratchet-corpus model at equal corpus size (beyond the ratchet's known n=1
  variance band). *Falsifier:* held-out lift within noise or not greater ⇒ diversity did not
  translate to model improvement ⇒ the swarm's write-back value is unproven.
- **p3 — the diversity is real, not cosmetic.** After semantic dedup (not just text), the
  swarm's distinct-solution advantage survives. *Falsifier:* the advantage is
  whitespace/comment/format variants that collapse under semantic dedup ⇒ inflated diversity,
  not genuine approach diversity.

## Kill rule (mechanical)
> **p1 falsifier fires** ⇒ stop. No stage B, no "tune the diversity prompt and retry" (the
> scaffold death spiral). The swarm may still ship as a user feature, but it is **not** a
> write-back improvement to the ratchet.
>
> **p1 holds, p2 fails** ⇒ diversity is real but doesn't help the model — record and stop;
> do not claim a ratchet improvement.
>
> **p1 holds, p3 fails** ⇒ the measured diversity was cosmetic; fix the dedup and re-gate
> before any stage B.

## Substrate & status (operator decisions taken 2026-09-09)
- **Substrate: terminal-bench-2 tasks adapted to git-repo form** (git repo + failing test +
  a verify command), split flatline vs gradient. Adaptation caveat: tbench tasks ship an
  encrypted `environment/protected.tar.gz.enc` (protected checker/solution) and container-only
  deps, so only a subset adapts cleanly to host git-repo form — the starter set is a
  hand-picked, cleanly-adaptable flatline+gradient handful (like the loop-control 4-task
  pilot), not all 92.
- **Diversity metric: built + tested** (`candidate_diversity` / `candidate_diff`,
  `drydock/swarm.py`).
- **NOT launched.** Blocked on: (1) the adapted git-repo task set (starter handful);
  (2) an eratchet candidate-diversity readout comparable to the swarm's (eratchet keeps its
  variant archive — expose distinct-verified-solution counts the same way). Stage A is cheap
  and gates everything; run it before committing any GPU time to stage B.

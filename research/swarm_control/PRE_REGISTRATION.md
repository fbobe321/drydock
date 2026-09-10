# Swarm vs eratchet — does COORDINATION beat blind parallelism? (PRE-REGISTERED)

**Written 2026-09-09, before any data.** Predictions + falsifiers are registered
machine-readably in `predictions.json`. This applies the loop-control discipline (the only
conclusions in this programme that never needed correction were the ones pre-registered to
disk) to the question: **will the multi-agent swarm improve the ratchet?**

## The honest starting point (read this first)
The prior from this programme's own bottleneck work is that **search is not the binding
constraint — write-back/heredity is** (`ratchet = evolution with heredity cut`; throughput
and training-solves-back-into-the-model were the levers, not fancier search). A swarm is a
fancier search. So the **null is favored**, and this control is designed to give the swarm
its best structural shot and to falsify honestly rather than to advertise an effect.

**Second honest point — there is not yet a real variable.** The shipped swarm MVP
(`drydock/swarm.py`) is: N diversity-injected Builders, each isolated in a git worktree,
run in parallel, then scored by the project verifier and judged on evidence. **That is
mechanically what `eratchet` already does** (λ diverse variants in worktrees across a server
pool, verifier-scored, keep the best). The things that would make a swarm *more than*
parallelism —
  1. **blackboard consumption** — an agent *reading* peers' verified discoveries mid-run
     (the MVP writes a blackboard but no agent reads it: it is write-only today);
  2. **adversarial critics** — agents whose job is to *falsify* a candidate the plain
     verifier passed;
  3. **dynamic reallocation** — a coordinator that kills losing lines and spawns on
     promising ones (the MVP fan-out is static);
— are all **Phase 2 and not built**. So "swarm vs eratchet" **today** would compare two
near-identical blind-parallel solvers and (correctly) find nothing. Running that would be a
null with no variable — exactly the pilot-#1 mistake in loop-control.

**Therefore this control tests coordination as a SINGLE ADDED VARIABLE over an
eratchet-equivalent baseline, one mechanism at a time**, starting with the cleanest and
cheapest to build:

> **Variable under test (round 1): blackboard consumption.** Baseline arm = blind parallel
> Builders (no cross-agent info) = the eratchet-equivalent. Treatment arm = identical, except
> each Builder is given, in its context, the *verified discoveries and rejected approaches*
> other Builders have already posted to the blackboard (§10/§31). One variable: whether an
> agent sees what its peers found. Everything else — count, budget, tasks, server, verifier,
> diversity injection — is identical.
>
> **CLEANEST APPARATUS (revised 2026-09-10): eratchet ± blackboard, same engine.** Rather
> than compare two *different* engines (swarm vs eratchet), run **eratchet with `--share`
> off vs on** — the identical loop, λ, generations, servers, verifier, and compute; the only
> difference is whether each generation's variants are handed the prior generations' scored
> attempts (`run_eratchet(share=...)`, grafting `_peer_notes_erx` into `_variant_prompt`).
> eratchet's variants were **blind within/across generations** (they saw only pass counts +
> a diversify nudge, plus diff-level crossover); `--share` makes generation *g* read
> generations *1..g-1*'s attempts+scores, turning blind parallel search into coordinated
> search. This is a tighter single-variable test than swarm-vs-eratchet and needs no separate
> baseline engine. **Hypothesis (honest):** the prior says search isn't the ceiling-lever, so
> the likeliest win is **compute efficiency** — the same result from fewer redundant variants
> — not a higher solve ceiling. That still matters (throughput is the gate) and is what would
> "revive" eratchet.

Rounds 2+ (only if round 1 clears its kill rule) add critics, then dynamic reallocation, each
as its own single-variable control.

## Matched compute is the integrity requirement
A swarm win only counts at **equal compute** — otherwise it just spent more inference (this
programme's throughput-is-the-gate lesson). `run_swarm` now reports `total_in_tokens`,
`total_out_tokens`, `total_turns` per run (commit adding matched-compute accounting). The
comparison is **valid only if the two arms are within ±20% total tokens**; a treatment win
outside that band is discarded as a compute confound, not credited. Both arms use the same N,
the same per-agent `max_turns`/`max_tool_calls`, the same tasks, and the same server.

## Tasks — give coordination its best case
Split the task set (both arms, paired per task):
- **Flatline subset** — monolithic checker, `total=1`, no gradient. The single-lineage
  ratchet structurally stalls here; **coverage + shared information** should help most. This
  is where coordination has its strongest structural case (p3).
- **Gradient subset** — partial-credit checkers, where a lone ratchet already climbs. Used
  as the contrast for p3.

Substrate note: the swarm and eratchet both operate natively on **a git repo + a verifier
command** (host, worktree isolation) — *unlike* the tbench campaign's `ratchet_solve.sh`
(docker-container isolation). So this control runs on git-repo tasks both tools consume
natively. Keeping the substrate aligned with terminal-bench-2 (the programme's only sanctioned
benchmark) is an **open operator decision** flagged below, not assumed here.

## Predictions (registered before data)
- **p1 — coordination lifts solves at matched compute.** Blackboard-consuming arm solves
  `>= baseline + 2` on the flatline subset, OR equal solves at strictly lower total tokens
  (both within the ±20% validity band for the solve-count comparison). *Falsifier:* within
  ±1 solves AND not cheaper ⇒ reading peers' findings adds nothing blind parallelism didn't
  ⇒ coordination is not the lever.
- **p2 — the lift is mediated by actually using shared information.** In treatment solves,
  the blackboard was **read and its content demonstrably shaped the solve** (an agent's
  trajectory references a peer discovery / avoids a peer's rejected approach) in `>=50%` of
  them. *Falsifier:* treatment beats baseline YET blackboard content was used in `<20%` of
  its solves ⇒ the gain is a seed/variance/parallelism confound, not coordination ⇒ do not
  credit the swarm.
- **p3 — coordination helps flatline more than gradient.** `(treatment − baseline)` solve
  delta is larger on the flatline subset than the gradient subset. *Falsifier:*
  `delta_flatline <= delta_gradient` ⇒ shared information is not where the value is.

## Kill rule (mechanical, enforced — the DPO-v4 lesson)
> **If p1's falsifier fires:** blackboard consumption is NOT wired into the ratchet/campaign,
> and the swarm ships as a **user feature only** (a nice parallel solver), not as a ratchet
> improvement. **No "try once more with a better coordinator prompt"** — that is the scaffold
> death spiral (more instruction, same ceiling) this programme has already hit twice
> (the scaffold; the Ledger, currently measuring 0 tool-calls / no lift in loop-control).
>
> **If p1 survives but p2's falsifier fires:** the effect is parallelism, which eratchet
> already provides — credit eratchet, not the swarm; re-run with more seeds before any claim.
>
> **If p1+p2 survive but p3 fails:** coordination helps broadly (fine) — do not restrict to
> flatline; proceed to the round-2 critic control.

## The arguably-more-important parallel test (write-back corpus)
Because the real lever is write-back, the swarm's highest-value contribution may not be a
better *solve* but a better *training corpus*: N parallel agents produce a **population of
verified diffs per task**, more diverse than one ratchet lineage. **Separate experiment
(not this control):** hold solves constant, measure candidate **diversity** (distinct
verified solutions per task) swarm vs eratchet, then measure whether self-distilling on the
more-diverse corpus lifts the base model more. Pre-registered separately when round-1 lands.

## Status
- Matched-compute accounting: **built + tested** (`run_swarm` metrics: total tokens/turns).
- **Blackboard consumption (the single variable): BUILT + tested — in BOTH engines.**
  - `run_swarm(share=...)` — waves where later agents read peers' verified attempts.
  - **`run_eratchet(share=...)` / `drydock eratchet … --share`** — the cleaner apparatus:
    the identical evolutionary loop with prior generations' scored attempts injected into each
    variant's prompt (`_peer_notes_erx` → `_variant_prompt`). `--share` off = today's blind
    eratchet = baseline; on = coordinated. One flag, one engine, exact matched compute.
  So the control's one variable now exists as a flag in the very engine whose value is in
  question, exactly as this pre-registration requires.
- Predictions: registered to `predictions.json` before data. ✓
- **NOT launched.** Remaining blocker:
  1. **Task set** — a flatline/gradient-split git-repo task set both tools run natively, kept
     aligned with terminal-bench-2 (operator decision).
  2. an eratchet candidate-diversity readout comparable to the swarm's (for the write-back
     companion experiment).
- Harness will mirror loop-control: paired per task, same server/budget, fleet-safe
  registration in `fleet_supervisor.sh::experiment_active()`, results to `results.csv`
  (`task, arm, solved, tokens, turns, shared_reads`), predictions resolved against it.

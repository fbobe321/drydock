# PRD — Continuous Learning Transformer Architecture (CLTA)

**Status:** Research / Experimental · **Version:** 0.2 (adapted from operator draft v0.1)
**Date:** 2026-09-08
**Owner:** `/data3/drydock-v3/research/clta/`
**Replaces:** the self-distillation program, closed 2026-09-08 after 7 attempts
(PRD_internalized_ratchet §19.1 — DPO v4 landed inside its pre-registered dead band).

**One-line thesis:** inference, in-context learning, and parameter adaptation as
*concurrent* processes across multiple timescales, rather than separate train/deploy modes.

---

## 0. What changed from the operator's v0.1, and why

The operator's draft is reproduced in intent throughout. Three edits, each with a reason:

1. **§20's core experiment is redesigned from ONE skill to MANY skills.** A single-skill test
   is one a correct implementation can *lose*: if demonstrations are retrievable, retrieval +
   in-context learning gets you the skill — that is precisely what transformers are best at.
   The honest advantage of fast weights over retrieval is **capacity and composition**, not raw
   capability on one skill (see §3.1). Testing one skill risks a misleading null.
2. **§9 (async concurrent inference/learning) is DEFERRED.** Double-buffered adapters, atomic
   swaps and CUDA streams are engineering, not science; they are known-solvable, and they
   cannot be validated before we know plasticity works at all. Build synchronous first.
3. **§11 (local learning / no-backprop) is DEFERRED.** It is its own multi-year research
   programme (forward-forward, predictive coding, synthetic gradients). Bundling it doubles
   the risk for no gain at prototype scale — full backprop on a 350M model with rank-8
   adapters is cheap. Revisit only if the MVP earns it.

## 1. Prior art — stand on it, do not re-derive it

This design independently reconstructs several published lines. Knowing which is which tells us
where the novelty actually is:

| draft section | existing work |
|---|---|
| §8 fast weights / plastic adapters | Schmidhuber fast weights; Ba et al. 2016; Miconi differentiable plasticity |
| §15 reread-after-update | **Test-Time Training**; TTT layers (2024) — the hidden state is itself a model updated at inference |
| §4 multi-timescale memory | **Titans** (2024–25) — attention as working memory + neural long-term memory written at test time by surprise |
| §7 learning controller | surprise-gated writes (Titans) |
| §11 local learning | forward-forward, predictive coding, DNI/synthetic gradients |
| §13/§16 consolidation, forgetting | continual-learning literature (EWC, replay, catastrophic forgetting) |

**Implication for scope:** RQ1 ("can parameter learning occur without destabilising inference")
and RQ6 ("can inference continue during async training") are **largely answered YES** in the
literature. Do not spend scarce compute re-proving them.

**What is genuinely un-built:** the *full hierarchy with an arbitrating controller* — a system
that decides, per observation, whether it belongs in context, episodic memory, fast weights, or
consolidation. That routing decision (RQ4) is the novel contribution, not any single mechanism.

## 2. Hypothesis (falsifiable)

> A transformer with rank-limited plastic adapters, written by a surprise-gated controller and
> consolidated on validation, will **retain and generalise skills after their demonstrations
> leave the context window**, and will do so on a *many-skill* workload where retrieval +
> in-context learning degrades.

**Pre-registered falsifier:** if the RAG/episodic baseline (§5 baseline 3) matches or beats the
plastic model on held-out instances of skills whose demonstrations have left context, at
equivalent compute, then fast-weight plasticity adds nothing over retrieval **for this workload**
and the programme stops at the MVP.

## 3. Architecture (MVP scope)

```
W_i^eff = W_i^base (frozen) + ΔW_i^fast (LoRA, rank 4-8) + ΔW_i^slow (consolidated)
```

- **Working memory** — ordinary context/attention. Fastest path, no parameter change.
- **Episodic memory** — external retrievable buffer of exact experiences.
- **Fast neural memory** — plastic LoRA adapters, updated online.
- **Consolidated memory** — stable adapters, written only after validation + replay.
- **Base weights** — frozen for the entire MVP. Non-negotiable (§7 safety).

### 3.1 What plasticity must buy over retrieval (the crux)
Retrieval + ICL is a *strong* baseline and will win on a single skill. Fast weights can only
justify themselves on axes retrieval structurally lacks:
- **Capacity** — after N demonstrations the skill lives in weights and costs **zero context
  tokens**; retrieval pays context on every use.
- **Composition** — 50 learned skills cannot co-occupy the context window; weights can hold
  them, and retrieval must *select*, which introduces a failure mode weights do not have.
- **Depth** — ICL conditions on demonstrations; weight updates can restructure representations.

**The experiment must therefore stress capacity and composition, or it tests the wrong thing.**

## 4. Core experiment (replaces draft §19/§20)

**Phase 0 — fact retention (sanity only, NOT evidence).** `ZORG = 1847`, context removed,
recall. Retrieval solves this trivially; it is a wiring check, and a "success" here means
nothing. Explicitly demoted from the draft so an early green light cannot be mistaken for a
result.

**Phase 1 — MANY-SKILL acquisition (the make-or-break test).**
- Generate **20–50 novel synthetic transformations** (deterministic string/number operations
  guaranteed absent from pretraining), each with k demonstrations.
- Present skills **sequentially**; each skill's demonstrations leave the context permanently
  after its introduction.
- Test **held-out instances of every skill, interleaved**, long after their demonstrations are
  gone.
- Metric: accuracy on unseen instances, per skill and aggregate — **not** recall of the
  demonstrations themselves.

**Phase 2 — correction.** Redefine a learned skill; measure adaptation speed and whether the
old behaviour is actually unlearned (draft §19 Phase D).

**Phase 3 — interference.** Does learning skill B damage skill A? (Draft §21 "forgetting".)

## 5. Baselines — BUILD AND MEASURE THESE FIRST

Per the operator's §22, and reordered so the cheap decisive ones come first:

1. **Frozen model** (floor)
2. **Frozen + long context** (all demos retained — the ceiling ICL can reach)
3. **Frozen + RAG/episodic retrieval** ← **the baseline that decides the project**
4. Frozen + conventional offline LoRA fine-tuning (the "just train it normally" control)
5. Online plasticity alone
6. Online plasticity + episodic
7. Full multi-timescale architecture

**Baselines 1–4 require NO new architecture and are buildable immediately.** They are also what
makes any later claim credible. **No plastic model is built until 1–3 are measured.**

## 6. Metrics
Retention · adaptation speed (experiences to behaviour change) · generalisation (unseen
instances) · interference (A after B) · recovery (can bad learning be rolled back) · compute
overhead vs inference-only · inference latency · learning efficiency (gain per update).

## 7. Safety / stability (non-negotiable in the MVP)
Base weights frozen · update magnitude clamped · adapter checkpoint before every write ·
immediate rollback · regression detection against a held-out replay set · every parameter
update logged with its trigger. The system must be able to answer **"why did the model
change?"** for every persistent write.

## 8. Prototype
100M–500M base model, rank 4–8 adapters, synchronous updates.

**Hardware note — this is the first workload this fleet is well-suited to.** A ~350M model with
rank-8 adapters trains in *minutes* on the RTX 8000 (48GB), versus ~19h for 100 DPO steps on the
31B. Turing's lack of bf16/FA2 barely matters at this scale. Iteration velocity rises ~2 orders
of magnitude, which is what makes running the full 7-baseline matrix repeatedly affordable.

## 9. Method discipline (carried over from the self-distillation post-mortem)

This project's track record is **7 nulls and 4 retracted claims**, every retraction caused by
measuring loosely before controlling tightly (PRD_internalized_ratchet §15.1, §17.1). CLTA is
far more ambitious, which makes self-deception *easier*, not harder — "the model learned!" is
exactly the claim that survives until someone runs the retrieval baseline.

Therefore, binding on this programme:
- **Baselines 1–3 measured before the plastic model exists.**
- **Every experiment registers a prediction and a falsifier before it runs**
  (`drydock/predictions.py`). The only conclusion this project made that required no correction
  was the one pre-registered to disk.
- **Kill rules live in the training loop as callbacks, not in the operator's head** — the DPO v4
  rule said stop at step 50 and the run reached 72 because nothing enforced it.
- **Uncertainties ranked by decision-impact** (`drydock/groundtruth.py`); attack the unknown
  that could invalidate the approach, not the cheapest one.

## 10. Open questions (from the draft's RQ list, minus those the literature answers)
- **RQ4** — what belongs in context vs episodic vs fast vs consolidated? *(the novel contribution)*
- **RQ5** — can fast weights acquire *skills* rather than memorise facts? *(Phase 1 decides)*
- **RQ7** — does rereading context after adaptation improve reasoning?
- **RQ9** — how should contradictory experience modify prior learning?
- **RQ10** — can continuous learning beat context + retrieval alone? *(the falsifier in §2)*

## 11. Status
- 2026-09-08 — PRD adapted from operator v0.1; scope cuts recorded; baseline harness next.

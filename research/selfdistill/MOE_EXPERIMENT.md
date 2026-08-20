# MoE-as-fast-generator → 31B distillation (experiment scaffold)

**Idea:** use the fast Gemma-4 **26B-A4B MoE** (~4B active, ~59 tok/s = ~4× the dense
31B) as a high-throughput GENERATOR on *easy* problems, then train the mission **31B
dense** on the verified traces. Cross-model (26B→31B), same family ⇒ traces are
format-native. NOT self-distillation; honesty holds (31B solves the eval itself).

## Status
- **Smoke gate:** PASSED — MoE does not loop on easy tasks (fix-git 2/2 r1, regex-log r2). ~59 tok/s.
- **Collect:** running on the idle .22 (MoE served as `moe-smoke` container). Corpus = `*_solved_traj.json`.
- **Harvest:** `moe_sft.jsonl` (full-trajectory chat SFT; existence of `_solved_traj.json` == verified).
- **Train + eval:** PENDING a free trainer (.20 RTX 8000 is serving campaign lanes).

## Pipeline
1. **Collect** (done/ongoing): `moe_collect_solve.sh <task> 3 500` with `LLM_URL=.22 MoE` → `*_solved_traj.json`.
2. **Harvest**: the inline harvester → `moe_sft.jsonl` (`{task, model, messages:[system,...]}`, ~170+ assistant turns).
3. **Train** (needs .20 free; compass `.venv-train`, HF base `/data3/Models/gemma-4-31B-it-hf`):
   QLoRA on `moe_sft.jsonl`, assistant-token loss, seq 1024. Two adapters to compare:
   - `moe-full` — train on the full ratchet-process trajectory (natural, diverse — the anti-format-memorize bet).
   - (optional) `moe-condensed` — via `general_condense.py`, to A/B against the heredity format-memorize failure.
4. **Convert + serve**: LoRA→gguf; serve MATCHED **HF-Q5** base and base+LoRA (a HF-trained LoRA is inert on QAT).
5. **Eval — THREE arms (all required)** against `eval_splits.json`:
   - **Generalization** — held-out tasks the 31B currently FAILS (`generalization_fail`, 35 on 2.1). Lift here = the win.
   - **Regression** — tasks the 31B currently PASSES (`regression_pass`, 54). Guard: reject the adapter if it degrades these.
   - **Control** — 31B trained on its OWN easy traces (campaign `ratchet/`), same eval → isolates whether cross-model MoE data helps vs. hurts.

## Gating criteria (what makes this a win vs. a null)
- **WIN:** generalization arm shows solves the base didn't have, WITHOUT regression, and ≥ the self-trace control.
- **NULL (headroom):** easy MoE traces only re-solve easy tasks the 31B already had → no hard-held-out lift (07-21 lesson).
- **REGRESSION (distilling-down):** the 31B degrades on the pass-set → the weak-model style hurt it; reject.

## Caveats
- Corpus is still SMALL (heredity needed 16–31 traces and still memorized). Grow before a real train.
- "Easy problems" here = easy tbench-2 tasks; genuinely easier/diverse volume may need tbench-1 or generated variants.
- Cross-scale: a result here characterizes the METHOD; validate the winning lever once at 31B scale before trusting it.

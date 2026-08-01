# Research — teacher-free self-distillation (Compass / Drydock)

Backup of the self-distillation research harness that lives operationally under
`/data3/tbench_local/` (not otherwise version-controlled). **Code only** — training data, logs,
runtime state, model weights, and the canary-bearing raw corpus are intentionally excluded.

## The formula (proven 2026-07-31)

Teacher-free self-distillation of an agentic coding model (Gemma-4-31B via the Drydock TUI):

1. **Collect** a verified `base✗ → assist✓` solve through the real TUI (best-of-N research assist).
2. **Condense** it to `task → winning file edits → run → verify` (`general_condense.py`) — raw
   agentic trajectories memorize but do NOT re-execute at inference; condensed solves transfer.
3. **Train** a QLoRA on the **matched** base; serve the LoRA on that same base (a LoRA is inert on a
   different checkpoint, e.g. a QAT quant).

Single-task reproduction is robust (3/3). **Multi-task composition is the open wall** — packing many
`task→solution` maps into one adapter memorizes (loss→0) but reproduces poorly (~0–2/7) regardless of
rank or target pruning. The remaining bet is **generalization via corpus volume**, boosted by the
corpus-RAG assist (P4).

## Layout

| Path | What |
|---|---|
| `selfdistill/selfdistill.sh` | closed-loop orchestrator: COLLECT→CONDENSE→TRAIN→EVAL→gated PROMOTE |
| `selfdistill/corpus_rag.py` | P4 — retrieve the most-similar past verified solve as an analogical assist hint (stdlib; never leaks current/held-out task) |
| `selfdistill/sd_train_oneshot_*.sh` | the diagnostic experiments (reprotest, hardmemo, condensed, composition, multirun) |
| `selfdistill/tg_notify_*.sh` | Telegram result notifiers — **token scrubbed**; set `TG_TOKEN`/`TG_CHAT` env to use |
| `selfdistill/{heldout,regression,pool*,base_solved}.txt` | frozen task splits |
| `collector/frontier_collect_n3.sh` | failure-gate → best-of-N research-assisted solve → verified harvest |
| `collector/tui_task_lib.sh` | drives tbench tasks through the real Drydock TUI (tmux) |
| `collector/general_condense.py` | the condenser (keeps final file edits + artifact-producing run step; prunes dead file-versions) |

## Restore notes

- Copy back to `/data3/tbench_local/…`, set `TG_TOKEN`/`TG_CHAT`, re-fetch the corpus (the scrubbed
  condensed dataset is on HF: `fbobe3/tbench-condensed-selfdistill-traces`).
- Matched serve base: a GGUF converted from the same HF checkpoint the LoRA was trained on.
- Full history + findings: `RESUME.md` (self-distill section) and `compass/docs/PRD.md`.

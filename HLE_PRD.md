# HLE PRD — drydock + GraphRAG + Deep Noir vs Humanity's Last Exam

**Status as of 2026-05-04 00:40 UTC.** Update on resume.

## Thesis

> drydock + GraphRAG + Deep Noir working together should be able to solve
> any problem.

HLE is the hardest possible PRD — explicitly designed to defeat frontier
models. Reaching a defensible local-26B score against it validates that
the three-leg architecture generalizes. Failures expose which leg is weak.

**Critical rule:** drydock IS the harness. Eval drives questions through
the real TUI. Never wrap the model directly. Every failure is a drydock-
or-leg bug to fix, not a harness limitation. See
`memory/feedback_drydock_is_the_harness.md`.

## The three legs and their current strength

| Leg | What it owns | State | Evidence |
|-----|--------------|-------|----------|
| **drydock** | agent loop, tool use, prompts, harness fixes | **strongest** | 70% SWE-bench file match; 49/52 PRD functional tests; 16K+ harness queue actively drained |
| **GraphRAG** | factual recall over indexed code/text | **medium** | infra works (1842 symbols, 75 chunks); only cwd source ingested; **no general knowledge corpus** |
| **Deep Noir** | reasoning-mode steering | **weakest** | scaffolding shipped (`drydock/steering/`), `LogitBiasSteeringApplier` wired, hook in agent_loop; **zero vectors trained** — hook is a no-op |

## Phase plan

### Phase 1 — baseline (in flight)

Goal: bare drydock + bare GraphRAG (cwd only) + zero Deep Noir vs HLE.
This number is the floor.

- ✅ `scripts/hle_eval.py` — single-file orchestrator
- ✅ `scripts/hle_eval_seed.jsonl` — 7 undergrad questions for pipeline validation
- ✅ Pipeline validated: 7/7 = 100% on seed (commit 7c3a2d9 + earlier)
- ✅ HF token landed at `~/.config/drydock/hf_token` (chmod 600)
- ✅ HLE accessible: 2500 questions total, 2158 text-only, 342 multimodal (skipped)
- 🟡 **N=200 overnight baseline running** (PID in `/tmp/hle_overnight.pid`,
      log `/tmp/hle_overnight.log`, results dir under `/data3/drydock/hle_results/`).
      Started 2026-05-04 00:46 UTC (restarted with Telegram wiring).
      Expected runtime ~10-16h based on q1's 6 min pace. Each question
      writes a line to `results.jsonl` incrementally — partial progress
      is always visible mid-run.
- 🟡 **Telegram pings configured** (commit ed7f6de, SOTA ref 45.9%):
  start ping fired; milestone every 50 completions; final + breakdown
  on completion; crash + resume command on error.
- ⏳ Phase 1 deliverable: a number — Gemma 4 + bare drydock baseline on HLE

Realistic baseline expectation per pre-run analysis:
- Bare 26B-A4B without retrieval/steering: ~5–10% on HLE
- Reaching ~22–25% would be defensible vs ~25–30% frontier scores

### Phase 2 — GraphRAG with knowledge corpus

Goal: ingest enough general knowledge that retrieve answers fact-recall
questions. Re-run, measure delta.

- ⏳ Identify corpus (Wikipedia subset / arXiv abstracts / textbook chunks)
- ⏳ Extend `drydock/graphrag/code_indexer.py` ingest path for non-code text
      (it already supports text via `text_indexer.py`; just needs corpus)
- ⏳ Bulk ingest into `~/.drydock/graphrag.sqlite`
- ⏳ Re-run HLE Phase 1 with corpus loaded; measure delta
- Expected delta: +3 to +7 points if corpus is good, +0 if not

### Phase 3 — Deep Noir reasoning vectors

Goal: train activation-steering vectors on reasoning-failure pairs from
admiral_history; deposit into `~/.drydock/steering/vectors/`; the
existing hook applies them. This is the user's research domain.

- ⏳ Extract pairs from admiral_history (model-output / correct-intervention)
- ⏳ Train vectors per direction: "verify-before-answer", "show-work-explicitly",
      "consider-units", "minimal-patch"
- ⏳ Deposit `.npy + .toml` per mode under `~/.drydock/steering/vectors/<mode>/`
- ⏳ Set `DRYDOCK_STEERING_MODES=<mode1>,<mode2>` env at TUI launch
- ⏳ Re-run HLE; measure delta vs Phase 2
- Expected delta: 0 to +3 points; high variance, open research

## Currently running / in flight

| Thing | PID | Log | Notes |
|-------|-----|-----|-------|
| stress harness | `/tmp/stress_pid.txt` (was 2219727) | `/tmp/stress_*.log` | 1d 9h+ uptime, ~76% through 1658-prompt suite |
| llm_balancer | 2462362 (rotates) | `/data3/drydock/logs/balancer.log` | :8001, ~10h uptime |
| HLE N=200 overnight | `/tmp/hle_overnight.pid` (was 2567027) | `/tmp/hle_overnight.log` | output `/data3/drydock/hle_results/run_*/`; expected 10-16h |

## Resume checklist (if connection dropped)

```bash
# 1. Where are we?
date -u
git -C /data3/drydock log --oneline --since="6 hours ago"
git -C /data3/drydock describe --tags --abbrev=0     # current PyPI tag

# 2. HLE run status
ps -p $(cat /tmp/hle_overnight.pid 2>/dev/null) -o pid,etime,comm
ls /data3/drydock/hle_results/
tail -50 /tmp/hle_overnight.log
n_done=$(wc -l < /data3/drydock/hle_results/run_*/results.jsonl 2>/dev/null)
echo "completed: $n_done / 200"

# 3. If HLE crashed mid-flight, RESUME (skip already-done IDs)
RUN_DIR=$(ls -td /data3/drydock/hle_results/run_* | head -1)
nohup /home/bobef/miniconda3/bin/python3 /data3/drydock/scripts/hle_eval.py \
    --source hle --limit 200 --shuffle --seed 42 --resume "$RUN_DIR" \
    > /tmp/hle_resume.log 2>&1 &

# 4. If complete, see the score
cat /data3/drydock/hle_results/run_*/summary.json | python3 -m json.tool

# 5. Infra health
ps -p 2462362 -o pid,etime
curl -s --max-time 3 http://localhost:8001/v1/models | head -1
gh issue list --repo fbobe321/drydock --state open --limit 5
```

## Known issues + workarounds

1. **`web_search` tool requires permission approval** by default
   (`ToolPermission.ASK`). In batch eval the harness can't see/respond
   to the prompt → session stalls. **Workaround:**
   `--dangerously-skip-permissions` flag passed by `hle_eval.py`.
   **Real fix:** auto-approve read-only tools when stdin is non-TTY
   or `DRYDOCK_BATCH_MODE=1`. Memory:
   `memory/project_hle_phase1_findings.md`.

2. **TUI input handler corrupts rapid char-by-char multi-line pexpect
   input.** Internal `\n` chars get partially eaten + spurious newlines
   inserted on long prompts. **Workaround:** single-line prompts in
   `hle_eval.py`. **Real fix:** debug `drydock/cli/textual_ui/` input
   buffer.

3. **Auto_release at 06:00/12:00/18:00/00:00 UTC overwrites site-packages.**
   In-flight HLE runs survive (each new question is a fresh TUI spawn that
   picks up the new binary), but if you direct-edit site-packages your
   changes vanish. Always commit to source. Pause via:
   `touch /data3/drydock/.pause_auto_release`.

4. **PRD contamination: model edits `PRD.md` mid-session.** HLE doesn't
   touch this — every HLE question gets a fresh empty cwd. Not a concern
   for HLE; relevant for shakedown PRD runs.

5. **Sessions take real time.** HLE questions involve web_search +
   multi-step reasoning. Seed q's took 30-60s; HLE q's appear to take
   5+ min each (q1: 5 min in and still working). Expect N=20 to take
   1-2 hours, N=100 overnight.

## Sentinels currently set

- `/data3/drydock_test_projects/.pause_watchdog` — watchdog cron paused
- `/data3/drydock/.pause_auto_release` — NOT set (auto_release is active)
- `/data3/drydock/research/STOP` — research loop sentinel (per gitignore)

## Tomorrow morning's first action

If overnight run completed:
1. `cat /data3/drydock/hle_results/run_*/summary.json` for the baseline number
2. If <10%, the diagnosis is in the per-question `verdict` and `judge_reasoning`
   fields. Sort by category to see if math/physics/CS dominate the failures.
3. Commit the results JSON (with HLE content redacted to just IDs+verdicts —
   never commit the question text per HLE license).
4. Decide Phase 2 corpus based on category distribution of failures.

If overnight run crashed:
1. `--resume` flag re-enters where it stopped (skip-by-id from results.jsonl).
2. Likely failure modes: vLLM OOM on a long-thinking question, balancer
   crash from a port conflict, drydock TUI hang on a tool error.
   Investigate via the per-question `tui_logs/<id>.tui.log` files.

## File map

```
/data3/drydock/
├── scripts/
│   ├── hle_eval.py                 # the orchestrator (this PRD's main artifact)
│   ├── hle_eval_seed.jsonl         # 7 hand-crafted seed questions
│   └── consume_retrieval_queue.py  # GraphRAG-leg autonomy (28× perf-fixed)
├── hle_results/                    # gitignored — per-run outputs
│   └── run_<ts>/
│       ├── config.json
│       ├── results.jsonl
│       ├── summary.json
│       ├── tui_logs/<id>.tui.log
│       └── work/<id>/              # per-question fresh cwd
├── HLE_PRD.md                      # this file
└── ~/.config/drydock/hf_token      # gated cais/hle access
```

## What "done" looks like for Phase 1

A number. With distribution. Per-category and overall. Writeup in this
PRD. Commit the writeup, not the questions. Decide Phase 2 from there.

# Resume — drydock session 2026-05-14

**Drop-in context for autonomous_review and human pickups. Updated when
state shifts meaningfully — not every commit.**

## TL;DR — system state right now

- **Model:** Gemma-4-26B-A4B-it-UD-**Q3_K_M.gguf** via llama.cpp Docker
  on `localhost:8000`. Q4_K_M was trialed 2026-05-12→14 and rolled back
  by operator decision (no HLE-correctness lift, ~9% slower per token).
- **Container:** `llamacpp-gemma4` (`restart=unless-stopped`). Restart
  via `bash /data3/Models/start_gemma4_llamacpp.sh`.
- **Balancer:** `llm_balancer.py` on :8001, round-robin to remus+romulus.
  Keepalive `*/5 * * * *` in cron.
- **PyPI release cadence:** `auto_release.sh` at 0/6/12/18 CDT
  (05/11/17/23 UTC). Pause via `touch /data3/drydock/.pause_auto_release`.
- **Continuous improvement loops:**
  | Cron | What |
  |------|------|
  | `*/10 * * * *` | `classify_pulse.sh` — log → dispatch queue |
  | `*/30 * * * *` | `autonomous_review.sh` — drain queues, ship fixes |
  | `0 * * * *`    | `stress_babysitter.sh` |
  | `45 * * * *`   | `hle_babysitter.sh` — 10-Q HLE batch per tick |

## STATUS COMMANDS (run these first when picking up)

```bash
# 1. Date + version
date -u
git -C /data3/drydock describe --tags --abbrev=0
git -C /data3/drydock log --oneline --since="6 hours ago" | head -10

# 2. Production model + balancer
curl -s --max-time 5 http://localhost:8000/props \
  | /home/bobef/miniconda3/bin/python3 -c "import sys,json; d=json.load(sys.stdin); print('model_path:', d.get('model_path','?'))"
curl -s --max-time 3 http://localhost:8001/v1/models | head -c 120

# 3. HLE state — both batch + lifetime
ps -p $(cat /tmp/hle_continuous.pid 2>/dev/null) -o pid,etime,comm 2>&1 | head -2
/home/bobef/miniconda3/bin/python3 /data3/drydock/scripts/hle_aggregate.py | head -12

# 4. Dispatch state (cleaned post-2026-05-14)
/home/bobef/miniconda3/bin/python3 /data3/drydock/scripts/dispatch_report.py --window 24h | head -20
/home/bobef/miniconda3/bin/python3 -m drydock.curiosity stats

# 5. Stress + autonomous_review heartbeat
tail -3 /data3/drydock/logs/autonomous_review.log
ps -p $(cat /tmp/stress_pid.txt 2>/dev/null) -o pid,etime 2>&1 | head -2

# 6. Sentinel state
ls /data3/drydock/.pause_* /data3/drydock_test_projects/.pause_* 2>/dev/null

# 7. Open GH issues
/home/bobef/miniconda3/bin/gh issue list --repo fbobe321/drydock --state open | head -10
```

## Active vectors (as of 2026-05-14)

Three vectors moving in parallel per CLAUDE.md "Current plan":

1. **HLE.** Continuous 10-Q babysitter loop. Lifetime: 5/163 = 3.1%
   (real number unknown — judge was 100% ERROR before bc12eee fix).
   Next batch after v2.8.28 lands will use arxiv corpus directly
   (edb61c9 — the biggest HLE fix of the day; shell script change
   takes effect on next cron tick, doesn't need PyPI release).
2. **GraphRAG.** arxiv corpus fallback shipped for both auto-prefetch
   and model-invoked retrieve tool. Project corpus stays primary
   for user TUI / stress; HLE uses arxiv directly.
3. **Deep Noir.** Sidecar M1-M4 coded, 91 tests green. Pair extraction
   was broken (admiral UUIDs vs session-dir short hashes) — fixed in
   49a9d92, then 50dcfb2 added content-scan path that doesn't need
   admiral. Data still thin (drydock's existing nudges suppress most
   stalls now, so contrastive pool is small). Vectors not yet trained.

## Recent shipping highlights (2026-05-14 push, since v2.8.27)

30+ commits — full list in `git log v2.8.27..HEAD --oneline`. The
load-bearing ones:

- `edb61c9` HLE was retrieving against the WRONG corpus (tool_agent
  artifacts on math questions). Babysitter now sets
  `DRYDOCK_GRAPHRAG_DB=/data3/arxiv_corpus/graphrag.sqlite` per-batch.
- `bc12eee` Judge had 100% ERROR rate across all 165 historical
  results — IndexError on empty `content`. Fix reads
  `reasoning_content` fallback, word-boundary verdict scan, retry on
  empty.
- `fa19d5a` + `352434f` Dispatch queue dedup. Was 73× amplified
  (14k thinking_stall entries / 193 unique). Now persistent
  fingerprint dedup; one-shot cleanup script available for retroactive.
- `15cf444` + `be20388` arxiv-corpus fallback in both
  auto_prefetch_retrieve and the model-invoked retrieve tool.
- `b49c450` Curiosity gap_detector dropping HLE template noise
  (FINAL, ANSWER, QUESTION accounted for ~180 false positives).
- `4b2ed7f` + `a5cf249` Hourly HLE babysitter cron + category rotation.
- `5fe42a0` Multi-batch hle_aggregate.py rollup.
- `c9061df` dispatch_report observability.
- `d9db430` rejudge_hle backfill script (run during quiet windows).

## Where to dig in next

Highest-leverage open items:

- **Watch the FIRST post-edb61c9 babysitter batch.** Should fire at
  next `:45` cron tick where prior batch isn't alive. Compare retrieve
  hits against historical (they were getting tool_agent help text;
  should now be arxiv abstracts).
- **Re-run hle_aggregate after rejudge.** Once v2.8.28 ships, run
  `scripts/rejudge_hle.py --apply --apply-to-summary` to backfill
  correct YES/NO on the 22 historical judge ERRORs. Lifetime score
  will move.
- **Q3 vs Q4 stall comparison.** Needs ~30 Q3 Math attempts accumulated.
  At 10 per hourly tick, that's ~3 hours of babysitter. Then compare
  against the Q4 30-Q overnight (2/30 = 6.7%, 87% stalls).
- **Deep Noir vectors.** Capture pipeline is ready (M3). Needs an
  operator-approved VRAM window to run the sidecar on real load, and
  enough contrastive pairs to train against. Pair pool is the real
  blocker — scan_sessions found 3 derailed across 15k sessions.

## Cron quick-toggles

```bash
# Pause continuous loops without touching the crontab:
touch /data3/drydock/.pause_hle_babysitter      # stops 45-min HLE batches
touch /data3/drydock/.pause_autonomous_loop     # stops 30-min review
touch /data3/drydock/.pause_auto_release        # stops 6h PyPI publish
touch /data3/drydock_test_projects/.pause_watchdog
# Remove the file to resume.
```

## Past-resume archive

Older snapshots live under `docs/archive/resume*.md`.

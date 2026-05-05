# Overnight Progress — 2026-04-20 → 2026-04-21

Drop-in status doc so a fresh Claude session (after disconnect) can
resume without relearning the last 24 hours. **Read this first, then
`git log --oneline -20` + `TaskList` for full picture.**

## What landed today (in order)

| Commit | What | Where it lives |
|---|---|---|
| 3cb957e | pexpect-buffer trim, banner detect, ESC+/clear, force-reset, checkpoint filter | scripts/ |
| f363ae1 | `DRYDOCK_AUTO_CONTINUE_DISABLE` env gate for agent_loop auto-Continue | drydock/core/agent_loop.py |
| 2e0431e | Admiral actuator via SIGUSR1 — watcher asks harness to recycle TUI | scripts/stress_watcher.py + stress_shakedown.py |
| 5c76801 | Wipe cwd down to fixtures on fresh stress start | scripts/stress_shakedown.py |
| aafa090 | **should_break_loop dead-code fix** — user turns actually end on text responses | drydock/core/agent_loop.py:876 |
| 1508c1b | SessionWatcher reads drydock's configured save_dir (was hardcoded ~/.drydock) | scripts/shakedown_interactive.py |
| c77c2e0 | Hourly stress_babysitter cron | scripts/stress_babysitter.sh + crontab |
| 0ae9d07 | Karpathy-autoresearch scaffold | research/ (kernel.py, experimenter.py, config_base.toml, mini_*) |
| 11cfe89 | Todo loop fix (#10) + Check 0 empty-result detector + realuser prompts + raw-md detector | drydock/core/tools/builtins/todo.py, agent_loop.py, scripts/ |
| fd7a160 | v2.6.145 release | PyPI + GitHub |
| aad7ac6 | Admiral sees raw-markdown leakage; DEPLOYMENT.md baseline | scripts/stress_watcher.py, DEPLOYMENT.md, README.md |
| 553926a | v2.6.146 release | PyPI + GitHub |

## Long-running processes (DO NOT KILL without checking)

| PID | Process | Notes |
|---|---|---|
| 2625579 | stress_shakedown (v10 restart #1) | auto-restarted by babysitter at 02:00 UTC after original died. Resuming from step 866. |
| child of 2625579 | drydock TUI | Recycled periodically by admiral actuator |
| 2366908 or newer | stress_watcher (v10) | Paired with current stress PID |
| 2251231 | admiral_probe on :8878 | Read-only dashboard endpoint |
| 1230765 | llm_balancer on :8001 | DO NOT KILL — proxies to vLLM, shared |
| cron | stress_babysitter.sh hourly | Auto-restarts stress if PID dies |

**v10 progress summary** (as of ~03:00 UTC 2026-04-21):
- Original run: reached idx 972/1658 (59%) with done=866, skip=105, recycle=72 — died at ~21:00 UTC
- Restart #1: resumed from step 866, currently at idx ~921, done=50, skip=5, recycle=3
- Net cumulative: ~920/1658, acceptance rate still ~95%
- Babysitter fired once (telegram sent), admiral actuator firing per run

## Meta-Harness integration — in progress

User approved the scope at ~03:00 local and asked for overnight build.
See `research/domain_spec.md` for the full design (just written).

**Approved mutation surface:**
- `drydock/core/prompts/gemma4.md` (system prompt)
- `drydock/core/prompts/cli.md` (alternate prompt)
- admiral knob TOML (existing 7 numerics in `config_base.toml`)
- `scripts/stress_shakedown.py` threshold constants
  (MAX_CONSECUTIVE_SKIPS_BEFORE_RESET, SESSION_RESET_EVERY, retry windows)
- `drydock/admiral/detectors.py` thresholds + patterns

**Approved frozen:**
- `drydock/core/agent_loop.py` (just stabilized, too risky)
- Tool implementations
- Provider config
- Core stress harness flow (pexpect loop, session tracking)

**Approved budget:**
- Per candidate: 20-min mini-stress (50 prompts) as search-set, 5-min kernel as held-out smoke
- Per 24 hr: up to ~30 candidates
- Full 1658-prompt stress is quarterly regression only

**Remaining overnight tasks:**
1. [in progress] Expand `config_base.toml` mutation surface (tasks #16)
2. [pending] Trace capture in `kernel.py` — save per-run artifacts to `research/traces/<exp_id>/` (task #17)
3. [pending] `research/proposer.py` — Opus-backed proposer (task #18)
4. [pending] `--proposer opus` flag in `experimenter.py` (task #19)
5. [pending] Commit + let auto_release ship (task #20)

**NOT running the experimenter overnight** — it would share vLLM with
v10 stress and starve both. Scaffolded ready-to-launch.

## Known issues to investigate (user-reported, not yet fixed)

### 2026-04-21 03:xx user reports drydock crash with 15+ API errors

User's quote:
```
✓ Read 0 line from prepare.py (truncated)
✓ Read 66 lines from prepare.py
✓ Read 66 lines from prepare.py
✓ Read 630 lines from train.py
✓ Read 19 lines from AGENTS.md

[6 consecutive API errors (round 1/3). Compacting and retrying. Last error: API error
from vllm (model: gemma4): LLM backend error [vllm]
status: 400 Bad Request
...]

[Stopping: 15+ API errors. The model cannot process this request.]
```

Plus drydock self-diagnoses: "You've written prepare.py 4 times this session.
If the file is oscillating Fix this before moving to the next file."

**This is the production version of the exact wedge pattern the stress
harness fixes.** Production drydock hits 400s, auto-compacts 2-3 times,
then gives up at 15+ errors. It DOES have recovery — the compaction
fires — but 15 errors in a row means compaction isn't finding enough
context to shrink.

**Root cause hypothesis:**
- User's session accumulated large read_file results (train.py 630 lines
  is substantial), plus prepare.py written 4 times = 4 copies of the file
  content in history.
- Auto-compact at 120000 tokens doesn't fire because the issue isn't
  raw token count, it's a single turn's payload exceeding vLLM's 131K
  context limit.
- Compaction per-attempt only truncates old tool results; doesn't
  dedupe repeat writes to the same file.

**Fix hypothesis (for tomorrow's session):**
- `_prune_duplicate_writes` already exists in `drydock/core/agent_loop.py`
  but only fires when the hard-block trips on a write_file call. Extend
  it to fire on ANY `write_file` call where the target already has ≥3
  prior write_file entries in history.
- Lower emergency-compact aggressiveness: when a 400 arrives, drop
  middle-of-history message runs even if recent, not just oldest
  truncation. The "keep first user + last 5" fallback is already there
  (lines 834-846 of agent_loop.py).
- Consider: hard-cap read_file results at 32KB per call regardless of
  `max_read_bytes` config when the session has had >50k tokens of
  tool results already.

**NOT fixing overnight** — this is in `agent_loop.py` which is marked
frozen for the meta-harness integration AND just got stabilized
yesterday. Investigate + propose fix in morning session.

## Auto-release state

**Paused flag:** NOT set. Auto-release cron fires at 0/6/12/18 UTC.
Next fire: 06:00 UTC (~03:00 local). Any commits before then ship in
v2.6.147.

**Version:** local == v2.6.146 (tagged fd7a160, aad7ac6 shipped).

## Monitoring for connection loss

If this Claude session disconnects:

1. **v10 stress continues** — babysitter cron handles restarts. Check
   `/tmp/stress_babysitter.log` and `~/.drydock/logs/admiral_history.log`.
2. **Meta-harness files** are committed incrementally. `git log --oneline`
   shows progress. Any staged-but-uncommitted changes are lost, but the
   `domain_spec.md` alone is enough to resume.
3. **No orphan processes** — all long-running jobs are daemonized via
   `nohup + disown`, they survive my death.

## For the next session (morning handoff)

- Read `research/domain_spec.md` for the full design.
- `git log --oneline fd7a160..HEAD` for commits since v2.6.145.
- Check v10 state: `tail /tmp/stress_babysitter.log`.
- Check meta-harness progress: `ls research/`, look for `proposer.py`
  and `traces/` — if both exist, the overnight build finished.
- **Priority for morning:** investigate the prepare.py-loop / 400-error
  wedge the user reported. Hypothesized fixes above.

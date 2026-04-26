# Resume — drydock session 2026-04-25

**You're picking up an in-flight stress run + bug-fix loop. Start here.**

## What you should do FIRST when reading this

1. Run the **STATUS COMMANDS** block below and confirm the stress run is alive
   and progressing. If it's dead, see the recovery section.
2. Continue the autonomous improvement loop: scan for new vLLM 400s, new
   admiral patterns, new GitHub issues, new bugs from session logs. When you
   find a real drydock bug, fix in source + write a regression test + commit.
3. Schedule a wakeup at the end of every meaningful action so you don't sit
   idle. The user said *"keep it busy"* — that's a continuous loop, not one
   task.
4. The user has memory entries you must respect:
   - `feedback_keep_it_busy_means_loop.md` — never sit idle
   - `feedback_proactive_github_issues.md` — `gh` lives at
     `/home/bobef/miniconda3/bin/gh`; check open issues every loop tick
   - `feedback_never_kill_drydock.md` — never broad-pkill drydock; only kill
     PIDs you spawned yourself
   - `feedback_no_tool_errors_for_loop_detection.md` — safety mechanisms must
     be advisory, never blocking
   - `project_git_remote_divergence.md` — local and origin diverge by design;
     don't `git push` directly
   - `feedback_local_proposer_only.md` — proposer must default to local vLLM,
     never silently phone home

## Where things stand (snapshot at 2026-04-25 ~20:30 UTC)

### Wins this session
| What | Result |
|------|--------|
| v2.7.5 shipped | yesterday's 4 GitHub issue fixes (#10/11/12/13) + PRD refresh |
| v2.7.6 shipped | search_replace APPEND fallback + **truncation JSON-validity fix** (the actual root cause of the recurring vLLM 400 spiral) |
| **v2.7.7 shipped** (commit 540c0e2) | **search_replace REFUSED-raw loop-breaker**: on 2nd+ consecutive REFUSED to same file, embed file head/tail in error + escalate directive (write_file overwrite=True OR proper SEARCH/REPLACE). 4 regression tests in `tests/tools/test_search_replace_refused_loop_breaker.py`. |
| Stress write rate | 10% pre-fix → ~44% (post v2.7.6) → 54% (post v2.7.7) → **74% sustained** as of 04:23 UTC |
| `retry_after_error:search_replace` | 14/6h pre-fix → **0** REFUSED-raw fires post-v2.7.7 |
| vLLM 400s | 315 per 30 min pre-fix → 0 per hour post-fix |
| Stress progress | 443/1658 (27% complete, 16h elapsed) |

### Active processes (do NOT kill these)
- **Stress harness:** PID 3713698, log `/tmp/stress_2000_1777119799.log`
- **Stress harness child TUI:** PID drifts (recycles every batch); look up
  with `pgrep -fa 'miniforge.*drydock$'`
- **llm_balancer:** PID 1230765 on `127.0.0.1:8001` — DO NOT KILL
- **admiral_probe:** PID 2251231 on `0.0.0.0:8878` — DO NOT KILL
- **vLLM Docker:** container `gemma4` on `127.0.0.1:8000`
- **Babysitter cron:** `/data3/drydock/scripts/stress_babysitter.sh` runs
  hourly, auto-restarts the harness if it dies before 1658/1658.

### Sentinels currently set
- `/data3/drydock/.pause_vllm_failover` — vllm_failover script is paused
- `/data3/drydock/research/STOP` — meta-harness experimenter paused
- `/data3/drydock/.pause_auto_release` — **NOT SET** (auto-release is live;
  next cron tick at 0/6/12/18 UTC will ship a v2.7.8 if you commit anything)

## STATUS COMMANDS — run these first

```bash
# Stress alive?
ps -p 3713698 -o pid,etime,rss && tail -5 /tmp/stress_2000_1777119799.log
# How many prompts done? (target: 1658)
grep -c '^\[' /tmp/stress_2000_1777119799.log
# vLLM 400s? (should be 0; if non-zero, regression)
docker logs --since 30m gemma4 2>&1 | grep -c JSONDecodeError
# Write rate last 100 prompts
grep -A 1 "^\[\s*[0-9]" /tmp/stress_2000_1777119799.log | grep "writes" | tail -100 | /home/bobef/miniforge3/envs/drydock/bin/python3 -c "
import sys, re
t=w=0
for line in sys.stdin:
    m=re.search(r'\+(\d+) writes', line)
    if m:
        t+=1
        if int(m.group(1))>0: w+=1
print(f'{w}/{t} = {100*w//max(t,1)}% with writes')"
# GitHub issues — fix any new ones
/home/bobef/miniconda3/bin/gh issue list --repo fbobe321/drydock --state open --limit 10
# Admiral fires last 30 min
tail -100 ~/.drydock/logs/admiral_history.log | grep "2026-04-25T1[3-9]\|2026-04-26T0[0-9]" | grep intervention | wc -l
# Local commits ahead of latest tag (these will ship at next auto-release)
git -C /dev/null log --oneline 2>/dev/null; cd /data3/drydock && git log --oneline $(git describe --tags --abbrev=0)..HEAD
```

## How to recover if the harness died

The babysitter handles this automatically each hour. If you need it sooner:

```bash
ls /tmp/stress_pid.txt && cat /tmp/stress_pid.txt
# If that PID is dead and prompts < 1658, the babysitter restarts on next cron tick.
# To force-restart now:
bash /data3/drydock/scripts/stress_babysitter.sh
```

If for some reason the stress run completed (idx >= 1658) and you want
another run, launch fresh with this exact configuration the babysitter
expects:

```bash
NEW_LOG=/tmp/stress_2000_$(date +%s).log
nohup /home/bobef/miniconda3/bin/python3 -u /data3/drydock/scripts/stress_shakedown.py \
    --cwd /data3/drydock_test_projects/403_tool_agent \
    --pkg tool_agent \
    --prompts /data3/drydock/scripts/stress_prompts_tool_agent_2000.txt \
    --max-per-prompt 300 --report-every 25 \
    > "$NEW_LOG" 2>&1 &
echo $! > /tmp/stress_pid.txt
```

## Improvement loop pattern

Every wakeup tick:

1. **Read the status commands above.** Confirm harness alive, vLLM 400s = 0,
   prompts increasing.
2. **Scan for new failures:**
   - New vLLM 400 cluster (`docker logs --since 30m gemma4 | grep JSONDecode`)
   - New admiral pattern that's NOT just `loop:` or `struggle:` (those are
     model-behavior, not drydock bugs)
   - New tool error type in current session log
   - New GitHub issue
3. **If a real drydock bug surfaces:**
   - Find it in source (`grep -rn` from `/data3/drydock/drydock/`)
   - Fix it
   - Write a regression test that fails without the fix
   - Commit with a descriptive message + Co-Authored-By line
   - Optionally trigger a manual release: `bash /data3/drydock/scripts/auto_release.sh`
   - If you commit but DON'T trigger manually, the next cron tick at
     0/6/12/18 UTC ships a vN+1 release automatically.
4. **Schedule a wakeup** for 15-60 min depending on how much you expect to
   change. Don't return "Awaiting direction" — the user explicitly told me
   that means I'm failing them.

## Open improvement areas (no urgent bugs found here yet)

- **SKIP rate at ~8%** — TUI wedges late in 30-prompt batches as context
  bloats. `_truncate_old_tool_results` keeps the last 6 messages full;
  could be tightened. Don't tweak harness `SESSION_RESET_EVERY` (that's
  metric gaming per CLAUDE.md), but a more aggressive truncation policy
  in drydock itself is fair game.
- **Model retries failing search_replace** when SEARCH text doesn't match
  the file (admiral catches this with `retry_after_error:search_replace`).
  Model behavior issue, but a stronger drydock-side response (e.g., force
  a re-read after 2 consecutive search_replace failures on the same path)
  could improve correctness.
- **GitHub issue queue** is currently empty — but check on every wakeup.

## What NOT to do

- **Don't `git push`** — local and origin diverge by design; deploy is via
  4 AM `deploy_to_github.sh` rsync cron (creates a synthetic Daily sync
  commit on origin).
- **Don't kill drydock processes by name** — only by specific PIDs you
  yourself spawned. The user has 7 idle TUIs that the stress harness
  legitimately reaped on its fresh-start; that was OK because they were
  3+ days idle. Don't repeat without confirmation.
- **Don't tweak harness parameters to inflate metrics** — fixes go in
  drydock source, not in `stress_shakedown.py`.
- **Don't lift sentinels without reason** — `.pause_vllm_failover` and
  `research/STOP` are deliberate. Ask the user before lifting.

## Big picture for the user when they read your first reply

When you start, give a tight one-screen summary of state:

- Stress prompts done / 1658
- Last 100 prompts write rate
- vLLM 400s last hour (should be 0)
- Last release shipped
- Any new GitHub issues
- What you're about to look at or do next

Then start the loop. Schedule the next wakeup before stopping.

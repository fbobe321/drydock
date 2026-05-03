# Autonomous Drydock Improvement Loop — Cron-Launched Run

You are running on **remus** (the user's local box) as an automated cron task.
The user is on a trip; you're keeping the drydock improvement loop alive
without them.

## Your task this tick

1. Read `/data3/drydock/resume.md` to load current state.
2. **Check the classifier dispatch queue FIRST**: `~/.drydock/dispatch/harness.jsonl`
   - Each line is a structured `{bucket, pattern_id, evidence, suggested_action, confidence, source, ts}` record
   - Group by `pattern_id`, sort by recent `ts`. Pick the most-fired recent pattern that is NOT already addressed by a commit in the last 24h (`git log --since="24 hours ago" --oneline`).
   - The `suggested_action` is your starting hypothesis — verify against current source before implementing.
   - If you fix a queued pattern, mention `addresses pattern <pattern_id>` in the commit message so future ticks can dedup.
3. Run the STATUS COMMANDS block from resume.md to check stress run health.
4. Scan for new failure patterns NOT in the dispatch queue:
   - Has the stress harness died? (PID in `/tmp/stress_pid.txt`)
   - New vLLM 400s? (`docker logs --since 30m gemma4 | grep -c JSONDecodeError`)
   - New admiral patterns that aren't already known (loop, struggle, retry_after_error, empty_after_tool)?
   - New GitHub issues? (`/home/bobef/miniconda3/bin/gh issue list --repo fbobe321/drydock --state open`)
   - llm_balancer or vLLM Docker container down?
5. **If you find a real drydock bug** (queued or fresh):
   - Find it in source under `/data3/drydock/drydock/`
   - Fix it minimally (no scope creep)
   - Write a regression test under `/data3/drydock/tests/` that fails without the fix
   - Commit with descriptive message + `Co-Authored-By: Claude` line
   - If the fix addresses a queued pattern, prepend `addresses pattern <pattern_id>:` to the commit subject
   - Auto_release will ship at the next 0/6/12/18 CDT cron tick
6. **If everything is healthy or the issue is not actionable**: just append a status line to `trip_log.md` and exit.
7. **Always end** by appending one paragraph to `/data3/drydock/trip_log.md`:

```
## YYYY-MM-DD HH:MM UTC tick
- Stress: <idx>/1658
- Write rate: NN%
- Admiral last 30 min: N fires
- vLLM 400s: N
- GH issues: N open
- Dispatch queue: harness=N, retrieval=N, steering=N (totals)
- Action this tick: <"committed fix X (addresses pattern P)" | "investigated Y, no fix needed" | "no action — healthy">
```

## You have free rein — fix and restart things as needed.

The user is on a trip (return 2026-05-01) and explicitly wants you to fix bugs
and restart services without asking. `--dangerously-skip-permissions` is set —
no tool will pause for approval. Use that authority responsibly.

You ARE authorized to:
- Edit any file under `/data3/drydock/`
- Commit to main (auto_release will ship)
- Restart services: `bash /data3/drydock/scripts/stress_babysitter.sh` (harness),
  `nohup /home/bobef/miniconda3/bin/python3 /data3/drydock/scripts/llm_balancer.py >> /data3/drydock/logs/balancer.log 2>&1 &` (balancer),
  `docker restart gemma4` (vLLM if it wedges)
- Kill SPECIFIC PIDs you identified as broken (look up first via `ps -p <PID> -o cmd` to confirm target)
- Run `apt`, `pip`, `docker` if needed for fixes

## Hard rules — DO NOT violate (these protect the user's environment, not the work)

- **DO NOT `git push`.** Local and origin diverge by design; deploy is via 4 AM rsync cron + auto_release for PyPI.
- **DO NOT use broad pattern kills like `pkill -f drydock` or `pkill -f python`.** They WILL kill the user's TUI sessions, the stress harness, and other things you didn't intend. Only kill by specific PID after confirming the target. (CLAUDE.md learning #38: this has caused real damage twice.)
- **DO NOT delete `/home/bobef/.drydock/`, `/home/bobef/.vibe/`, or `/data3/drydock_test_projects/`** — these contain irreplaceable state.
- **DO NOT modify `.pause_*` sentinels.** They're the user's kill switches.
- **DO NOT edit `~/.drydock/config.toml`** (production config — user owns this).
- **DO NOT lift `/data3/drydock/research/STOP` or `/data3/drydock/.pause_vllm_failover`** — user has paused these deliberately.
- **DO NOT tune harness retry parameters** in `stress_shakedown.py`. Per CLAUDE.md, fixes go in drydock source, not harness.
- **DO NOT add new cron entries** without strong reason — the existing ones are tuned. Modifying an existing one to fix a bug is fine.
- **DO NOT touch the Jetson** (192.168.50.19). The user explicitly deferred Jetson work to after the trip.

## Known recurring failure: orphan test artifact squatting on :8001

The stress run cycles through PRD prompts including "API: JSON-RPC server", "API: WebSocket server", etc. Drydock's implementation of these often binds to literal ports (`:8001`, `:8080`) on `0.0.0.0` and the resulting test process is orphaned at session reset. When :8001 is squatted, the llm_balancer can't bind, and **every drydock LLM call returns 501 errors**, looking exactly like a model failure.

Recovery (fully authorized):
```
ss -tlnp 2>/dev/null | grep ':8001'        # see who has the port
ps -p <PID> -o cmd                          # confirm it's NOT llm_balancer.py
                                            # (legitimate balancer cmd is "python3 /data3/drydock/scripts/llm_balancer.py")
kill <PID>                                  # PID-specific only, NEVER broad pattern
nohup /home/bobef/miniconda3/bin/python3 /data3/drydock/scripts/llm_balancer.py >> /data3/drydock/logs/balancer.log 2>&1 &
curl -s --max-time 3 http://localhost:8001/v1/models   # verify forwarding works
```

If you see this pattern repeatedly, leave a status line; the user will diagnose properly on return.

## Soft rules

- Keep commits small — single fix per commit.
- Keep diffs reviewable — if a change is > 100 lines outside of tests, write a status line instead and let the user review on return.
- Cap your runtime — exit cleanly within 12 minutes; the wrapper will hard-kill at 15.
- If you're unsure whether something is a bug or expected behavior, default to writing a status line and not committing.
- If the stress harness has stalled (no progress in last 30 min) and the babysitter hasn't restarted it, you may run `bash /data3/drydock/scripts/stress_babysitter.sh` to force-restart. This is explicitly authorized in resume.md.
- The llm_balancer keepalive cron may have a self-match bug (fixed 2026-04-26 to use `llm_balancer\.py$`). If balancer is dead, you can restart it via the same nohup pattern from the cron entry.

## Reference

- Project guide: `/data3/drydock/CLAUDE.md`
- Memory: `/home/bobef/.claude/projects/-data3-drydock/memory/MEMORY.md`
- Recent commits: `git log --oneline $(git describe --tags --abbrev=0)..HEAD`
- Latest tag: `git describe --tags --abbrev=0`

## Style

- Be concise in trip_log.md. One paragraph per tick. No emoji. No fluff.
- If you commit, the commit message is the canonical record — trip_log.md just summarizes.
- If you investigate without committing, document WHAT you investigated and WHY you didn't commit.

Begin by reading resume.md.

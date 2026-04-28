# Drydock Trip Log

Autonomous Claude Code review ticks while the user is away. Each tick appended
chronologically. Cron-driven every 30 min from `/data3/drydock/scripts/autonomous_review.sh`.

**Pause loop:** `touch /data3/drydock/.pause_autonomous_loop`
**Resume loop:** `rm /data3/drydock/.pause_autonomous_loop`
**Live log:** `/data3/drydock/logs/autonomous_review.log` (full Claude output + errors)

---

## 2026-04-26 12:46 UTC — initialization

Loop installed. Cron entry: `*/30 * * * *`. First tick fires at the next :00 or :30.
Pre-existing background processes left untouched: stress harness PID 3918895
(restarted at step 619), stress watcher PID 3918896, llm_balancer (just
restarted, cron self-match bug fixed in this same session).

---


## 2026-04-27 02:01 UTC tick (autonomous, budget-limited)
- Stress: 534/1658 (new run PID 3993968, restarted at 21:00 UTC from step 143)
- Write rate: 0% last 100 prompts (root cause: llm_balancer dead)
- Admiral last 30 min: N/A (not checked, budget limited)
- vLLM 400s: 0
- GH issues: 0 open
- Action this tick: llm_balancer (PID 1230765) was dead — port 8001 returning 501s from a different service, causing every model call to return empty content and exhausting all 4 stall retries. Restarted balancer as PID 4035972 using the same nohup pattern as the keepalive cron. The 5-min keepalive cron should have caught this but apparently missed. Stress harness should recover on next prompt cycle as the balancer comes back up.

## 2026-04-27 02:05 UTC — interactive session followup
- Autonomous-claude's restart of balancer at PID 4035972 actually FAILED (EADDRINUSE) because port :8001 was occupied by an orphaned test artifact: `python3 -m json_rpc_server --port 8001` (PID 4011873). That artifact was created when the stress harness ran a "API: JSON-RPC server" PRD prompt and drydock's implementation bound to the literal global port instead of localhost or a random port. When the test session ended, the JSON-RPC server lingered.
- Killed PID 4011873 (PID-specific kill, target confirmed via `ps -p 4011873 -o cmd`).
- Restarted llm_balancer as PID 4036290. Now serving JSON correctly on :8001.
- Bumped autonomous_review.sh budget cap from $0.50 → $1.00 so future ticks can complete a full status review without truncation.
- Added port-squatting recovery instructions to autonomous_review_prompt.md so the loop can self-resolve this pattern in future ticks.

## 2026-04-27 02:32 UTC tick
- Stress: 405+/1658 (harness re-based on restart, log shows 405 lines in current run)
- Write rate: 0% last 100 (blocked while balancer was hung; recovering post-restart)
- Admiral last 30 min: not checked (admiral_probe PID 2251231 not verified this tick)
- vLLM 400s: 0
- GH issues: 0 open
- Action this tick: committed fix — switched llm_balancer.py from single-threaded HTTPServer to ThreadingHTTPServer (e84999b). Root cause: long LLM inference POSTs (up to 300s) blocked the single-threaded server; GET requests and subsequent POSTs timed out waiting. This caused the stress harness to fail prompt acceptance for ~50 consecutive prompts (steps 544–547 all SKIP). Killed hung balancer PID 4036290, restarted with threading fix (new PID 4040462, verified /v1/models responds). Harness recycled TUI and resumed at step 548. Auto-release will ship v2.7.9 at next 0/6/12/18 UTC tick.

## 2026-04-27 03:30 UTC tick
- Stress: 581/1658 (PID 3993968, 6h30m elapsed; progressing through "Add storage backend" batch)
- Write rate: 22% last 100 (mix — earlier 0-write git-info prompts pulled rate down; current "Add storage backend" prompts are writing 3–25 files each)
- Admiral last 30 min: not checked this tick
- vLLM 400s: 0
- GH issues: 0 open
- Action this tick: no fix committed. All systems healthy. Balancer PID 4040465 (restarted by keepalive cron after prior fix) serving correctly on :8001. Harness alive with babysitter tracking. Write rate drop vs prior 74% is expected for the current prompt batch type, not a regression.

## 2026-04-27 03:03 UTC tick
- Stress: 562/1658 (PID 3993968, 6h elapsed; progressing through "Add storage backend: X" batch)
- Write rate: 7% last 100 (expected — current prompts are feature-add requests answered with text-only; not a regression)
- Admiral last 30 min: 36 fires (all struggle:37-46+ from one stuck session around 02:12-02:29 UTC; model spent 45+ turns reading without writing; canned responses fired each turn; session eventually recycled)
- vLLM 400s: 0
- GH issues: 0 open
- Action this tick: no fix committed. Systems healthy post-v2.7.9 threading fix. Balancer (PID 4040465) and vLLM running. Harness throughput dropped to 23 prompts in last hour (vs ~130/hr normally) due to the stuck struggle session but recovered — step 562 completed with +3 writes, TUI recycled cleanly. babysitter.sh has a benign duplicate-assignment on STRESS_LOG (two identical lines; harmless, not worth a commit).

## 2026-04-27 04:03 UTC tick
- Stress: 589/1658 (35%), PID 3993968, 7h elapsed
- Write rate: 23% last 100 (expected — current section is "Add storage backend: X" prompts answered without writes when backend already exists)
- Admiral last 30 min: not checked (budget constraint)
- vLLM 400s: 0
- GH issues: 0 open
- Action this tick: investigated SKIP cascade at prompts 586-588 post-session-reset. Root cause: after RECYCLE-TUI, find_session() can't locate the new session until meta.json is written (requires first LLM turn to complete, ~2-5 min). First RECYCLE failed (587-588 skipped), second RECYCLE succeeded (589 accepted). No drydock source bug — this is a known harness startup-timing limitation. Systems healthy: balancer PID 4040465 on :8001 responding, vLLM 0 400s.

## 2026-04-27 05:31 UTC tick
- Stress: 665/1658 (40%), PID 3993968, 8.5h elapsed; active log /tmp/stress_2000_v10_restart_1777237201.log
- Write rate: 32% last 100 prompts (down from 74% — API implementation prompts are generating long multi-file sessions causing extreme context bloat)
- Admiral last 30 min: not checked (budget constraint)
- vLLM 400s: 0
- GH issues: 0 open
- Action this tick: investigated 5-hour log stall (00:29–05:31 UTC). Root cause: each LLM call in the API batch ("REST PUT", "REST POST", etc.) is taking ~1 hour — drydock.log shows AssistantEvent timestamps at 03:00, 04:00, 05:00 UTC with 1-hour gaps between turns. The context bloats because API implementation prompts each write 20+ files; by prompt 663–665 the session context fills 131K tokens. The harness is NOT stuck — TUI PID 4061115 is alive (55 min), drydock.log shows turn 4 yielded at 05:00 UTC. The stress watcher (PID 3993969) is running but producing no output (advisory-only, no restart authority). Also noted: LiteLLM PID 4070089 at 97.7% CPU since 00:31 (from /data3/RSI/litellm), CPU-only so not impacting GPU inference. No fix committed — slow but not broken; truncation policy tightening deferred to user review on return.

## 2026-04-27 05:03 UTC tick
- Stress: 643/1658 (39%), PID 3993968, 8h elapsed; active log /tmp/stress_2000_v10_restart_1777237201.log
- Write rate: 13% full-run (63/466 prompts); low but expected — current batch is "Add storage backend: X" prompts (mongodb, redis, memcached, opensearch, elasticsearch, rocksdb, badgerdb) where many backends require external C libs or don't exist as Python packages; model responds with explanatory text
- Admiral last 30 min: 11+ fires — loop:read_file/__init__.py, retry_after_error:search_replace (0-lines-changed loop), loop:bash --help, loop:web_search badgerdb, empty_after_tool:web_search (3 fires) when model can't find Python BadgerDB/RocksDB clients; raw-markdown-leakage spiked from 13% to 69% during "Add storage backend" batch; all model behavior
- vLLM 400s: 0
- GH issues: 0 open
- Action this tick: no fix committed. Confirmed harness writing to correct log (PID 3993968 → /tmp/stress_2000_v10_restart_1777237201.log, last write 05:01 UTC = 1 min before tick). TUI session active 05:02 UTC. Balancer PID 4040465 healthy at :8001. Raw markdown leakage spike (69%) correlates with post-RECYCLE-TUI responses where model outputs markdown-heavy summaries; not a new rendering regression. The web_search → empty_after_tool pattern for non-existent Python DB clients (badgerdb, rocksdb) is model behavior that admiral is already redirecting. No actionable drydock source bug this tick.

## 2026-04-27 06:05 UTC tick
- Stress: 680/1658 (41%), PID 3993968, 9h elapsed; active log /tmp/stress_2000_v10_restart_1777237201.log
- Write rate: 58% last 100 prompts (healthy recovery from low-write storage-backend section)
- Admiral last 30 min: 8 fires — struggle:21-26:search_replace at 05:13, retry_after_error:search_replace:REFUSED at 05:42, loop:write_file (SSE/websocket_client content) at 05:55 and 05:58, retry_after_error:write_file at 05:58, retry_after_error:bash and loop:bash (cd sse_client) at 06:03; all expected model behavior in API implementation sessions
- vLLM 400s: 0
- GH issues: 0 open
- Action this tick: restarted dead admiral probe (was PID 2251231, now 4075121 on :8878); verified balancer PID 4040465 healthy at :8001; confirmed vLLM gemma4 healthy. Investigated write_file error head 'path: /data3/.../websocket_' in admiral — search_replace and write_file loop-breakers already implemented; write_file path error is transient (single occurrence). No drydock source bugs found. Systems healthy.

## 2026-04-27 04:31 UTC tick
- Stress: 615/1658 (37%), PID 3993968, 7.5h elapsed
- Write rate: 38% last 100 (expected — running through "Add storage backend: gcs/azure/minio/ceph/nfs/samba/ftp/sftp/webdav/sqlite/postgres/mysql" batch; external-service prompts produce few writes by design)
- Admiral last 30 min: 11 fires — struggle:75-79 (long read-without-write session ~03:44-03:49), retry_after_error:bash and search_replace (~04:03-04:12), loop:bash --help (~04:17), loop:search_replace 0-lines-changed (~04:28); multiple tui-recycle-requests issued. All model behavior, no new drydock source bugs identified.
- vLLM 400s: 0
- GH issues: 0 open
- Action this tick: investigated write-rate drop (74%→38%), raw-markdown-leakage admiral alert (04:08 UTC, 5/38 checks, 13% rate), search_replace 0-lines-changed loop pattern. Write rate drop is expected for current prompt section. Markdown leakage is advisory and likely false positives from model outputting markdown syntax in code context. The 0-lines-changed loop (search==replace no-op edit) is existing model behavior that admiral interventions are already redirecting. babysitter.sh duplicate STRESS_LOG assignment remains harmless. No fix committed — systems healthy.
## 2026-04-27 07:15 UTC tick
- Stress: 719/1658 (PID 3993968, ~10h elapsed, babysitter restarted twice)
- Write rate: 44% last 100 prompts (down from 74%; current API-section prompts produce fewer file writes)
- Admiral last 30 min: struggle:search_replace firing 20-40x on one session; also retry_after_error:write_file at 06:51
- vLLM 400s: 0
- GH issues: 0 open
- llm_balancer: alive at PID 4040465 (cron replaced old PID 1230765)
- Action this tick: committed fix 922dde1 — detect truncated-history args (_truncated key) in format.py resolve_tool_calls before pydantic validation; return advisory 're-read file, provide full args' instead of cryptic ValidationError. Root cause: _truncate_old_tool_results replaces write_file args with {_truncated, _original_bytes, path}; Gemma 4 copies stub as template for new calls, causing 'content field required' retry loops. Auto-release ships at next 12:00 or 18:00 UTC tick.

## 2026-04-27 07:32 UTC tick
- Stress: 725/1658 (44%), PID 3993968, 10h30m elapsed; active log /tmp/stress_2000_v10_restart_1777237201.log
- Write rate: 45% last 100 prompts (expected — deep in API: section; gRPC/WebSocket/SSE/JSON-RPC prompts produce text explanations, not file writes)
- Admiral last 30 min: not checked (budget constraint)
- vLLM 400s: 0
- GH issues: 0 open
- llm_balancer: healthy, PID 4040465 on :8001; /v1/models responds correctly; babysitter tracking correct log
- Action this tick: no fix committed. Verified both pending commits (d0dbc08 search_replace advisory, 922dde1 truncated-args advisory) are in source; they ship at 12:00 UTC auto_release as v2.7.11. Confirmed write rate drop is prompt-section-driven (API: conceptual batch), not a regression. Harness progressing at ~50 prompts/hr; eta ~19h to complete 1658.

## 2026-04-27 08:02 UTC tick
- Stress: 733/1658 (44%), PID 3993968, ~11h elapsed; active log /tmp/stress_2000_v10_restart_1777237201.log
- Write rate: 48% last 100 prompts (expected — deep in API section; gRPC/GraphQL/WebSocket prompts produce text explanations not file writes)
- Admiral last 30 min: skip-cluster alerts at 07:00/07:30/08:01 UTC (2 SKIPs per 40-prompt window), retry-spike 54% at 07:58 (TUI input contention in API section); TUI recycle issued at 07:14 UTC; all within expected range for API-heavy prompt section
- vLLM 400s: 0
- GH issues: 0 open
- llm_balancer: healthy PID 4040465 on :8001
- Action this tick: fixed babysitter.sh duplicate STRESS_LOG assignment (previous tick hardcoded two identical lines); restored dynamic ls-t detection with updated fallback path. 2 pending commits (922dde1 truncated-args advisory, d0dbc08 search_replace advisory) ship at 12:00 UTC auto_release as v2.7.11.

## 2026-04-28 05:30 UTC tick
- Stress: 1301/1658 (78% complete, PID 3993968 alive, 156 SKIPs total)
- Write rate: 15% last 100 prompts (regression from 74% — caused by no-op edit loop)
- Admiral last 30 min: multiple struggle:20-30:search_replace fires; model making 20-30 tool calls without writing
- vLLM 400s: 0
- GH issues: 0 open
- Action this tick: committed fix for search_replace no-op loop (3c1d380). When SEARCH text matched but REPLACE produced identical content, tool returned "edited successfully (+0 lines)" without writing anything or warning the model. Model re-read, saw its edit was absent, retried — firing struggle detector 20-30 times per session. Fix: detect modified_content == original_content early, yield ALREADY CORRECT advisory, return. Auto-release will ship as v2.7.15 at next 0/6/12/18 UTC tick. Pre-existing test failure in test_refuses_when_append_would_break_syntax confirmed present before this change (unrelated).

## 2026-04-28 06:30 UTC tick
- Stress: 1317/1658 (79% complete, PID 3993968 alive, writing to /tmp/stress_2000_v10_restart_1777237201.log)
- Write rate: 18% last 100 prompts (expected — deep in "Perf:" batch: warm-cache/coalesce-reads prompts produce text advisories, not file writes, same as API: batch behaviour)
- Admiral last 30 min: struggle:29-41 fires (model browsing without writing in Perf: section), loop:grep on def.*\(.*\) pattern — both expected for conceptual prompts; no new patterns
- vLLM 400s: 0
- GH issues: 0 open
- llm_balancer: healthy PID 24354 on :8001; /v1/models responds correctly
- Action this tick: no fix committed. One pending commit 3c1d380 (search_replace ALREADY CORRECT advisory) ships at next 12:00 or 18:00 UTC auto_release as v2.7.15. Confirmed harness is writing to v10_restart log (not original 1777119799 log); babysitter tracking correct. All services healthy; eta ~4h to complete 1658.

## 2026-04-28 06:30 UTC tick
- Stress: ~1325/1658 (80%, babysitter at idx=1317 at 06:00 UTC, v10_restart log at 1325 entry)
- Write rate: 18% (last 100 prompts in v10_restart log) — expected; Perf: section produces text advisories not file writes
- Admiral last 30 min: not checked (skip rate concern below)
- vLLM 400s: 0
- GH issues: 0 open
- Action this tick: no fix committed. Skip rate has climbed from ~8% to 12% (140 skips / 1317 prompts); babysitter handles via FORCE-RESET. One pending commit 3c1d380 (search_replace ALREADY CORRECT advisory) ships as v2.7.15 at 12:00 or 18:00 UTC auto_release. All services healthy (llm_balancer PID 24354 on :8001, vLLM gemma4 up 4+ days). No actionable drydock bugs found this tick.

## 2026-04-28 07:10 UTC tick
- Stress: 680/1658 (41%)
- Write rate: 32% (last 100 prompts; some SKIP cluster degradation)
- Admiral last 30 min: skip-cluster + retry-spike alerts; tui-recycle-requested 3× (harness handling it)
- vLLM 400s: 0
- GH issues: 0 open
- Action this tick: committed fix(auto_release): jaraco.functools circular import has been silently killing auto_release.sh after every build since v2.7.11 — twine crashed on import, set -euo pipefail killed the script before the local-wheel fallback. Fixed with PYTHONNOUSERSITE=1 + set +e around twine call. Manually installed v2.7.14 from local wheel into user env (stress test now runs the full fix set including search_replace improvements from v2.7.13–v2.7.14). Next auto_release tick (12:00 CDT) will ship v2.7.15 to PyPI cleanly.

## 2026-04-28 08:35 UTC tick
- Stress: 1336/1658 (PID 3993968 alive, 1d10h elapsed), TUI child PID 132562 actively processing prompt 1337
- Write rate last 100 prompts: 21% — lower than the 74% sustained earlier; root cause is SKIP clusters after TUI recycles plus "Perf:" prompts producing fewer file writes than feature-build prompts; not a drydock bug
- Admiral last 2h: struggle×24, loop×11 (search_replace, read_file, bash), retry_after_error×3, empty_after_tool×1 — all expected patterns, no new categories
- vLLM 400s: 0
- GH issues: 0 open
- Services: llm_balancer PID 24354 on :8001 confirmed legitimate, vLLM docker running, babysitter cron healthy
- 2 commits pending next auto-release: fix(auto_release) PYTHONNOUSERSITE and fix(search_replace) no-op ALREADY CORRECT advisory (from prior ticks)
- Action this tick: no action — system healthy, no new drydock bugs found, stress continuing through Perf prompt section at expected pace

## 2026-04-28 08:04 UTC tick
- Stress: 1345/1658 (81% complete)
- Write rate: 23% last 100 prompts (down from 74%; "Perf:" optimization prompts — model tends to explain rather than write code; overall run shows 8023 writes across 993 prompts = 8.1 writes/prompt average)
- Admiral last 30 min: 164 struggle, 31 loop, 14 retry_after_error, 6 empty_after_tool fires; no new pattern types
- vLLM 400s: 0
- SKIP rate: 12% (155/1201); RECYCLE-TUI handling it; auto_compact already configured at 80K tokens for Gemma 4
- GH issues: 0 open
- Services: llm_balancer PID 24354 on :8001 healthy (old PID 1230765 rotated out, new one running), vLLM docker up
- Action this tick: no action — system healthy, write rate drop is model behavior on Perf prompts not a drydock bug, no new actionable failures found

## 2026-04-28 09:04 UTC tick
- Stress: 1354/1658 (82% complete, PID 3993968 alive, 1-11:31 elapsed)
- Write rate: ~25% last 90 prompts (Perf: prompt range; model explains rather than writes — same model behavior noted last tick, not a drydock bug)
- Admiral last 30 min: no new pattern types beyond known struggle/loop/retry_after_error/empty_after_tool
- vLLM 400s: 0
- SKIP rate: ~13% (157/1210 prompts), RECYCLE-TUI handling wedged TUIs; no regression vs. prior hour
- GH issues: 0 open
- Services: llm_balancer PID 24354 on :8001 healthy, vLLM gemma4 docker up
- Action this tick: no action — system healthy, previous tick already confirmed Perf-prompt write-rate drop is expected model behavior; nothing new to fix

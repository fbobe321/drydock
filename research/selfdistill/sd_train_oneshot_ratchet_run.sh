#!/usr/bin/env bash
# sd_train_oneshot_ratchet_run.sh — test the RATCHET collection mechanism (Dawkins cumulative selection).
# Borrows the GPU from the (futile, stuck-at-7) grind, runs ratchet_solve.sh on a few UNSOLVED,
# NON-held-out tasks, then relaunches the loop. If the ratchet cracks tasks best-of-N can't, it's the
# volume-ceiling breaker. Named sd_train_oneshot* so the watchdog defers relaunch while it holds the GPU.
set -u
SD=/data3/tbench_local/frontier/selfdistill
CONTAINER=llamacpp-gemma4-31b
TASKS_TO_RATCHET=(${RATCHET_TASKS:-kv-store-grpc nginx-request-logging})
MAX_ROUNDS="${MAX_ROUNDS:-5}"; ROUND_BUDGET="${ROUND_BUDGET:-1200}"
LOG="$SD/ratchet_run.log"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

say "===== RATCHET RUN — tasks: ${TASKS_TO_RATCHET[*]} (rounds=$MAX_ROUNDS budget=${ROUND_BUDGET}s) ====="
# stop the grind loop + collectors (keep the base server up — the ratchet drives the base model on it)
for p in $(pgrep -f 'selfdistill.sh loop' 2>/dev/null); do kill "$p" 2>/dev/null; done
pkill -f 'frontier_collect_n3.sh' 2>/dev/null; sleep 2
for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep '^ddt_'); do docker rm -f "$c" >/dev/null 2>&1; done
# ensure a base server is up (QAT is fine — the ratchet uses the base model)
if ! curl -sf http://localhost:8000/health >/dev/null 2>&1; then
  say "no server — starting base"; bash "$SD/selfdistill.sh" serve base >/dev/null 2>&1 || true
fi
curl -sf http://localhost:8000/health >/dev/null 2>&1 && say "server healthy" || say "WARN: server not healthy"

: > "$SD/ratchet/ratchet_results.csv" 2>/dev/null || true
echo "task,solved,best,total" > "$SD/ratchet/ratchet_results.csv"
for t in "${TASKS_TO_RATCHET[@]}"; do
  say "--- ratchet: $t ---"
  bash "$SD/ratchet_solve.sh" "$t" "$MAX_ROUNDS" "$ROUND_BUDGET" 2>&1 | tail -1
done

say "===== RATCHET RUN DONE ====="; column -s, -t "$SD/ratchet/ratchet_results.csv" 2>/dev/null | tee -a "$LOG"
# restore the grind loop
docker rm -f "$CONTAINER" >/dev/null 2>&1
say "relaunching self-distill loop"
setsid nohup bash "$SD/selfdistill_launch.sh" >> "$SD/selfdistill.log" 2>&1 &
say "loop relaunched"

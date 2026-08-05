#!/usr/bin/env bash
# sd_train_oneshot_ratchet_hard.sh — push the RATCHET further on genuinely HARD tasks.
# Unlike sd_train_oneshot_ratchet_run.sh this does NOT relaunch the self-distill loop at the end
# (operator asked to stop the normal run and keep progressing the ratchet). Named sd_train_oneshot*
# so the 15-min watchdog DEFERS relaunch for the whole sweep (it greps 'sd_train_oneshot').
#
# Substrate: hard, non-held-out, non-regression, richly-graded tasks so the pawl/rollback across
# rounds actually fires (round-1 full-pass unlikely; the model climbs 3->5->8 across rounds).
set -u
SD=/data3/tbench_local/frontier/selfdistill
TASKS_TO_RATCHET=(${RATCHET_TASKS:-db-wal-recovery llm-inference-batching-scheduler compile-compcert})
MAX_ROUNDS="${MAX_ROUNDS:-6}"; ROUND_BUDGET="${ROUND_BUDGET:-1200}"
LOG="$SD/ratchet_hard_run.log"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

say "===== RATCHET HARD SWEEP — tasks: ${TASKS_TO_RATCHET[*]} (rounds=$MAX_ROUNDS budget=${ROUND_BUDGET}s) ====="
# base server must stay up (the ratchet drives the base model on it)
if ! curl -sf http://localhost:8000/health >/dev/null 2>&1; then
  say "no server — starting base"; bash "$SD/selfdistill.sh" serve base >/dev/null 2>&1 || true
fi
curl -sf http://localhost:8000/health >/dev/null 2>&1 && say "server healthy" || say "WARN: server not healthy"

RESULTS="$SD/ratchet/ratchet_hard_results.csv"
echo "task,solved,best,total" > "$RESULTS"
for t in "${TASKS_TO_RATCHET[@]}"; do
  say "--- ratchet: $t ---"
  bash "$SD/ratchet_solve.sh" "$t" "$MAX_ROUNDS" "$ROUND_BUDGET" 2>&1 | tail -1
  # ratchet_solve appends to ratchet/ratchet_results.csv; mirror this sweep's rows too
  tail -1 "$SD/ratchet/ratchet_results.csv" >> "$RESULTS" 2>/dev/null
done

say "===== RATCHET HARD SWEEP DONE (loop intentionally NOT relaunched) ====="
column -s, -t "$RESULTS" 2>/dev/null | tee -a "$LOG"

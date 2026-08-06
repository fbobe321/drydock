#!/usr/bin/env bash
# sd_train_oneshot_ratchet_pool.sh — measure the RATCHET's reach across the tbench-2 pool.
# Runs ratchet_solve.sh over every unsolved/non-held-out/non-regression candidate (ranked by
# check-count, richest gradient first). RESUMABLE (skips tasks already in the master CSV) and
# THROUGHPUT-aware (ABORT_FLAT_ROUNDS bails tasks with no gradient early). Answers: "how many NEW
# tbench tasks can the ratchet crack now?" Named sd_train_oneshot* so the watchdog defers (it's
# paused anyway). Does NOT relaunch the self-distill loop.
set -u
SD=/data3/tbench_local/frontier/selfdistill
CANDS="${CANDS:-$SD/ratchet/pool_candidates.txt}"
MASTER="$SD/ratchet/ratchet_pool_results.csv"
MAX_ROUNDS="${MAX_ROUNDS:-6}"; ROUND_BUDGET="${ROUND_BUDGET:-1200}"
export ABORT_FLAT_ROUNDS="${ABORT_FLAT_ROUNDS:-2}"   # no gradient after 2 rounds → skip the rest
LOG="$SD/ratchet_pool_run.log"; HB="$SD/ratchet_pool_heartbeat.txt"; DONE="$SD/ratchet_pool.done"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

rm -f "$DONE"
[ -f "$MASTER" ] || echo "task,solved,best,total" > "$MASTER"
total_c=$(grep -cve '^[[:space:]]*$' "$CANDS")
say "===== RATCHET POOL SWEEP — $total_c candidates (rounds=$MAX_ROUNDS budget=${ROUND_BUDGET}s abort_flat=$ABORT_FLAT_ROUNDS) ====="

solved_n=0; done_n=0
while IFS= read -r task; do
  task="$(echo "$task" | tr -d '[:space:]')"; [ -z "$task" ] && continue
  # resume: skip tasks already recorded
  if grep -q "^$task," "$MASTER" 2>/dev/null; then
    grep -q "^$task,1," "$MASTER" && solved_n=$((solved_n+1)); done_n=$((done_n+1)); continue
  fi
  # base server must be up (ratchet drives the base model)
  if ! curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    say "server down — restarting base"; bash "$SD/selfdistill.sh" serve base >/dev/null 2>&1 || true; sleep 8
  fi
  say "--- [$((done_n+1))/$total_c] ratchet: $task ---"
  bash "$SD/ratchet_solve.sh" "$task" "$MAX_ROUNDS" "$ROUND_BUDGET" 2>&1 | tail -1
  row=$(tail -1 "$SD/ratchet/ratchet_results.csv" 2>/dev/null)   # ratchet_solve appended this task's row
  case "$row" in "$task,"*) echo "$row" >> "$MASTER";; *) echo "$task,ERR,,"  >> "$MASTER";; esac
  echo "$row" | grep -q "^$task,1," && solved_n=$((solved_n+1))
  done_n=$((done_n+1))
  echo "[$(date '+%F %T')] done=$done_n/$total_c solved=$solved_n last=$row" > "$HB"
  say "progress: done=$done_n/$total_c solved=$solved_n"
done < "$CANDS"

say "===== RATCHET POOL SWEEP DONE — solved $solved_n / $done_n attempted ====="
{ echo "SOLVED tasks:"; grep ',1,' "$MASTER" | cut -d, -f1 | sed 's/^/  /'; } | tee -a "$LOG"
touch "$DONE"

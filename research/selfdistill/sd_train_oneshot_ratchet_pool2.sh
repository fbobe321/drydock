#!/usr/bin/env bash
# sd_train_oneshot_ratchet_pool2.sh — parallel-safe ratchet pool sweep (one STREAM of two).
# Reads each task's result from its OWN per-task log (race-free vs a shared CSV tail), appends to
# the shared master under flock, and works only its assigned candidate file. Two of these run
# concurrently over disjoint, interleaved task halves to ~halve wall-clock. Resumable (skips tasks
# already in the master). Named sd_train_oneshot* so the watchdog defers (it's paused anyway).
#
# Env: CANDS=<file>  STREAM=a|b   [MAX_ROUNDS=6 ROUND_BUDGET=1200 ABORT_FLAT_ROUNDS=2]
set -u
SD=/data3/tbench_local/frontier/selfdistill
CANDS="${CANDS:?set CANDS}"; STREAM="${STREAM:?set STREAM}"
MASTER="$SD/ratchet/ratchet_pool_results.csv"; LOCK="$SD/ratchet/ratchet_pool.lock"
MAX_ROUNDS="${MAX_ROUNDS:-6}"; ROUND_BUDGET="${ROUND_BUDGET:-1200}"
export ABORT_FLAT_ROUNDS="${ABORT_FLAT_ROUNDS:-2}"
LOG="$SD/ratchet_pool_${STREAM}.log"; HB="$SD/ratchet_pool_${STREAM}_heartbeat.txt"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
say(){ echo "[$(date '+%F %T')] [$STREAM] $*" | tee -a "$LOG"; }

rm -f "$SD/ratchet_pool_${STREAM}.done"
[ -f "$MASTER" ] || { flock "$LOCK" -c "echo 'task,solved,best,total' > '$MASTER'"; }
n=$(grep -cve '^[[:space:]]*$' "$CANDS"); i=0
say "===== POOL STREAM $STREAM — $n tasks (rounds=$MAX_ROUNDS budget=${ROUND_BUDGET}s abort_flat=$ABORT_FLAT_ROUNDS) ====="

while IFS= read -r task; do
  task="$(echo "$task" | tr -d '[:space:]')"; [ -z "$task" ] && continue
  i=$((i+1))
  grep -q "^$task," "$MASTER" 2>/dev/null && { say "[$i/$n] $task already recorded — skip"; continue; }
  if ! curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    say "server down — restarting base"; bash "$SD/selfdistill.sh" serve base >/dev/null 2>&1 || true; sleep 8
  fi
  say "[$i/$n] --- ratchet: $task ---"
  bash "$SD/ratchet_solve.sh" "$task" "$MAX_ROUNDS" "$ROUND_BUDGET" 2>&1 | tail -1
  # parse THIS task's own log (race-free): "===== RATCHET <task> DONE: solved=S best=B/T ====="
  d=$(grep 'DONE: solved=' "$SD/ratchet/${task}.log" 2>/dev/null | tail -1)
  s=$(echo "$d" | grep -oE 'solved=[0-9]+' | grep -oE '[0-9]+'); s=${s:-ERR}
  bt=$(echo "$d" | grep -oE 'best=[0-9]+/[0-9]+' | grep -oE '[0-9]+/[0-9]+'); b=${bt%/*}; t=${bt#*/}
  flock "$LOCK" -c "echo '$task,${s},${b:-},${t:-}' >> '$MASTER'"
  [ "$s" = "1" ] && say "🎉 SOLVED $task ($b/$t)"
  echo "[$(date '+%F %T')] [$STREAM] $i/$n last=$task,${s},${b:-}/${t:-}" > "$HB"
done < "$CANDS"

say "===== POOL STREAM $STREAM DONE ====="
touch "$SD/ratchet_pool_${STREAM}.done"
# when BOTH streams are finished, signal the combined done-file (idempotent)
if [ -f "$SD/ratchet_pool_a.done" ] && [ -f "$SD/ratchet_pool_b.done" ]; then
  solved=$(awk -F, 'NR>1 && $2==1' "$MASTER" | wc -l)
  tot=$(awk -F, 'NR>1' "$MASTER" | wc -l)
  say "BOTH STREAMS DONE — solved $solved / $tot tasks"
  { echo "SOLVED tasks:"; awk -F, 'NR>1 && $2==1{print "  "$1}' "$MASTER"; } | tee -a "$LOG"
  touch "$SD/ratchet_pool.done"
fi

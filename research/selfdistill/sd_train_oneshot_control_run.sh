#!/usr/bin/env bash
# sd_train_oneshot_control_run.sh — one stream of the NO-RATCHET control over a task list.
# Runs control_noratchet.sh (single attempt) per task on .22/llama.cpp, reads the result from
# each task's own log (race-free), appends to the shared master under flock. Resumable.
# Two run concurrently over interleaved halves (llama.cpp -np 2). Named sd_train_oneshot* so the
# watchdog defers.
set -u
SD=/data3/tbench_local/frontier/selfdistill
CANDS="${CANDS:?set CANDS}"; STREAM="${STREAM:?set STREAM}"
RES="$SD/control/control_results.csv"; LOCK="$SD/control/control.lock"
LOG="$SD/control_${STREAM}.log"; BUDGET="${BUDGET:-1200}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
say(){ echo "[$(date '+%F %T')] [$STREAM] $*" | tee -a "$LOG"; }
mkdir -p "$SD/control"
[ -f "$RES" ] || flock "$LOCK" -c "echo 'task,solved,best,total' > '$RES'"
rm -f "$SD/control_${STREAM}.done"

n=$(grep -cve '^[[:space:]]*$' "$CANDS"); i=0
say "===== CONTROL (no ratchet) STREAM $STREAM — $n tasks, single attempt each ====="
while IFS= read -r t; do
  t="$(echo "$t" | tr -d '[:space:]')"; [ -z "$t" ] && continue
  i=$((i+1))
  grep -q "^$t," "$RES" 2>/dev/null && { say "[$i/$n] $t already recorded — skip"; continue; }
  curl -sf http://localhost:8000/health >/dev/null 2>&1 || { say "server down — waiting"; sleep 10; }
  say "[$i/$n] --- control: $t ---"
  bash "$SD/control_noratchet.sh" "$t" "$BUDGET" 2>&1 | tail -1
  r=$(grep "RESULT $t " "$SD/control/${t}.log" 2>/dev/null | tail -1)
  s=$(echo "$r" | grep -oE 'solved=[0-9]+' | grep -oE '[0-9]+'); s=${s:-ERR}
  bt=$(echo "$r" | grep -oE '[0-9]+/[0-9]+' | tail -1); b=${bt%/*}; t2=${bt#*/}
  flock "$LOCK" -c "echo '$t,${s},${b:-},${t2:-}' >> '$RES'"
  [ "$s" = "1" ] && say "  ✓ solved (no ratchet)" || say "  ✗ not solved in one shot (${b:-?}/${t2:-?})"
done < "$CANDS"

say "===== CONTROL STREAM $STREAM DONE ====="
touch "$SD/control_${STREAM}.done"
if [ -f "$SD/control_a.done" ] && [ -f "$SD/control_b.done" ]; then
  sol=$(awk -F, 'NR>1 && $2==1' "$RES" | wc -l); tot=$(awk -F, 'NR>1' "$RES" | wc -l)
  say "BOTH DONE — model+harness solved $sol/$tot of the 29 in a SINGLE attempt (no ratchet)"
  touch "$SD/control.done"
fi

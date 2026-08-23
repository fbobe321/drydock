#!/usr/bin/env bash
# baseline_sweep.sh — the HONEST single-attempt baseline for terminal-bench 2.1.
#
#   baseline_sweep.sh [K] [STREAMS]        (default K=1, STREAMS=2)
#
# WHY: the campaign's 63/89 (70.8%) is produced by verifier-guided test-time SEARCH —
# up to 16 rounds x 2-3 variants, snapshot/rollback carrying state between attempts, and
# the hidden checker's score fed back into the next prompt, retried over weeks. Leaderboard
# entries are 5 INDEPENDENT fresh attempts with no score feedback. So 70.8% is a statement
# about the SYSTEM, not the model, and is not leaderboard-comparable.
#
# This measures the comparable quantity: ONE attempt per task, no rounds, no snapshot, no
# rollback, no continuation, no score feedback (control_noratchet.sh). The publishable claim
# is the DELTA: "Drydock + verifier-guided search lifts Gemma-4-31B from X% to 70.8%".
#
# DESIGN NOTE (statistics): for an AGGREGATE pass rate, task-to-task variance dominates
# run-to-run variance, so k=1 over ALL 89 tasks estimates the mean more efficiently than
# k=5 over a 25-task subset (89 runs, ~+-5%, vs 125 runs, ~+-9%). Raise K later for
# publication-grade per-task rates.
set -u
SD=/data3/tbench_local/frontier/selfdistill
TB=/data3/tbench_local/tasks/terminal-bench-2
OUT="$SD/baseline"; mkdir -p "$OUT"
CSV="$OUT/baseline_results.csv"
LOG="$OUT/sweep.log"
LOCK="$OUT/.csv.lock"
K="${1:-1}"; STREAMS="${2:-2}"
export LLM_URL="${LLM_URL:-http://192.168.50.20:8000/v1}"
export DD_VER="${DD_VER:-3.1.15}"
BUDGET="${BUDGET:-900}"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
[ -f "$CSV" ] || echo "task,attempt,passed,total,solved" > "$CSV"

_done(){ awk -F, -v t="$1" -v k="$2" '$1==t && $2==k{f=1} END{exit f?0:1}' "$CSV" 2>/dev/null; }

run_one(){   # run_one <task> <attempt>
  local t="$1" k="$2" res ps pt solved
  _done "$t" "$k" && return 0                     # resumable
  # collision guard: the campaign lane may own this container
  docker ps -a --format '{{.Names}}' | grep -qx "ddt_$t" && { say "SKIP $t (container busy)"; return 0; }
  say "[k$k] $t"
  LLM_URL="$LLM_URL" DD_VER="$DD_VER" bash "$SD/control_noratchet.sh" "$t" "$BUDGET" >>"$LOG" 2>&1
  # control_noratchet only logs the result; parse its per-task log
  res=$(grep -E "^\[.*\] RESULT $t " "$SD/control/${t}.log" 2>/dev/null | tail -1)
  solved=$(echo "$res" | grep -oE 'solved=[01]' | cut -d= -f2); solved="${solved:-0}"
  ps=$(echo "$res" | grep -oE '[0-9]+/[0-9]+$' | cut -d/ -f1); ps="${ps:-0}"
  pt=$(echo "$res" | grep -oE '[0-9]+/[0-9]+$' | cut -d/ -f2); pt="${pt:-0}"
  ( flock 9; echo "$t,$k,$ps,$pt,$solved" >> "$CSV" ) 9>"$LOCK"
  say "  -> $t k$k: $ps/$pt solved=$solved"
}

# build the work list: every task x K attempts, interleaved so streams stay balanced
mapfile -t TASKS < <(ls -d "$TB"/*/ | xargs -n1 basename | sort)
say "BASELINE sweep: ${#TASKS[@]} tasks x K=$K over $STREAMS streams (server $LLM_URL)"
WORK=(); for k in $(seq 1 "$K"); do for t in "${TASKS[@]}"; do WORK+=("$t:$k"); done; done

stream(){   # stream <idx> — take every STREAMS-th item
  local idx="$1" i=0 item
  for item in "${WORK[@]}"; do
    if [ $(( i % STREAMS )) -eq "$idx" ]; then run_one "${item%%:*}" "${item##*:}"; fi
    i=$((i+1))
  done
}
for s in $(seq 0 $((STREAMS-1))); do stream "$s" & done
wait
say "BASELINE DONE -> $CSV"
"$SD/baseline_report.py" 2>/dev/null || true

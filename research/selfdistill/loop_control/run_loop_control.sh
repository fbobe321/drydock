#!/usr/bin/env bash
# run_loop_control.sh — execute the PRE-REGISTERED ledger-vs-plain-ratchet control.
#
# Runs both arms of loop_control/PRE_REGISTRATION.md on the same tasks/budget on the
# freed .20 lane, PAIRED per task (plain first, then with-loop), same engine
# (ratchet_solve.sh), one variable = whether the first-principles Ledger tool is pinned.
#
# SAFE BY CONSTRUCTION — no session is killed and no in-flight job is orphaned:
#   · this script's name is in fleet_supervisor.sh::experiment_active(), so while it runs
#     the supervisor SKIPS all refill + keepalive (no new jobs enqueued, .20 stays free);
#   · it WAITS for the .20 workers to finish their current jobs and go idle before taking
#     the lane — the 2026-08-21 disaster was killing drivers mid-job; this does the opposite;
#   · it drives ddt_<task> directly and ABORTS on any container the fleet still owns.
# On exit the supervisor auto-resumes the campaign on the next tick.
set -u
SD=/data3/tbench_local/frontier/selfdistill
ROOT=/data3/tbench_local
export TASKS="$ROOT/tasks/terminal-bench-2"
export LLM_URL="${LLM_URL:-http://192.168.50.20:8000/v1}"
export DD_VER="${DD_VER:-3.1.25}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
source "$ROOT/tui_task_lib.sh"
PY=/home/bobef/miniconda3/bin/python3
OUT="$SD/loop_control"; mkdir -p "$OUT"
MAX_ROUNDS="${1:-6}"; ROUND_BUDGET="${2:-900}"
TASKFILE="${3:-$OUT/tasks.txt}"
CSV="$OUT/results.csv"
LOG="$OUT/run.log"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
[ -f "$CSV" ] || echo "task,arm,solved,best,total,ledger_used" > "$CSV"

# ── wait for the .20 lane to go idle (workers not 'running' a job) ────────────
wait_for_idle(){
  local waited=0 max=10800   # up to 3h; the 3 in-flight jobs are bounded by their budgets
  while [ "$waited" -lt "$max" ]; do
    local a b
    a=$(cat "$SD/cluster/worker_20a.hb" 2>/dev/null)
    b=$(cat "$SD/cluster/worker_20b.hb" 2>/dev/null)
    if ! echo "$a" | grep -q "running " && ! echo "$b" | grep -q "running "; then
      say ".20 lanes idle — claiming the lane"
      return 0
    fi
    [ $((waited % 300)) -eq 0 ] && say "waiting for .20 to drain (20a/20b still running a job)…"
    sleep 30; waited=$((waited+30))
  done
  say "WARN: .20 did not go idle within ${max}s — proceeding anyway with the abort-if-busy guard"
  return 0
}

# ── run one arm of one task; append a tagged result row ──────────────────────
run_arm(){   # <task> <arm: plain|loop>
  local task="$1" arm="$2" ctr="ddt_$1" pins="" used="n/a"
  if docker ps -a --format '{{.Names}}' | grep -qx "$ctr"; then
    say "ABORT $task/$arm: $ctr exists (fleet owns it) — skipping"; return 0
  fi
  [ "$arm" = "loop" ] && pins='"Ledger"'
  say "RUN $task arm=$arm (LOOP_PINS=[${pins}], rounds=$MAX_ROUNDS budget=${ROUND_BUDGET}s)"
  LOOP_PINS="$pins" LLM_URL="$LLM_URL" DD_VER="$DD_VER" \
    bash "$SD/ratchet_solve.sh" "$task" "$MAX_ROUNDS" "$ROUND_BUDGET" >>"$LOG" 2>&1
  # best-effort usage signal: did the model actually write to the Ledger's stores?
  if [ "$arm" = "loop" ]; then
    if docker ps --format '{{.Names}}' | grep -qx "$ctr"; then
      used=$(docker exec "$ctr" sh -c 'ls /app/.drydock/ 2>/dev/null | grep -E "ledger|bottleneck|prediction" | tr "\n" ";"' 2>/dev/null)
      [ -z "$used" ] && used="none-written"
    else
      used="torndown"
    fi
  fi
  local row; row=$(awk -F, -v t="$task" '$1==t{r=$0} END{print r}' "$SD/ratchet/ratchet_results.csv" 2>/dev/null)
  local solved best total
  solved=$(echo "$row" | awk -F, '{print $2}'); best=$(echo "$row" | awk -F, '{print $3}'); total=$(echo "$row" | awk -F, '{print $4}')
  echo "$task,$arm,${solved:-ERR},${best:-},${total:-},${used}" >> "$CSV"
  say "RESULT $task/$arm solved=${solved:-ERR} ${best:-?}/${total:-?} ledger=${used}"
  ddt_down "$task" >/dev/null 2>&1
}

say "===== LOOP CONTROL START (rounds=$MAX_ROUNDS budget=${ROUND_BUDGET}s) ====="
say "tasks: $(tr '\n' ' ' < "$TASKFILE")"
wait_for_idle
while read -r task; do
  [ -z "$task" ] && continue
  run_arm "$task" plain
  run_arm "$task" loop
done < "$TASKFILE"
say "===== LOOP CONTROL DONE — see $CSV ====="
column -t -s, "$CSV" 2>/dev/null | tee -a "$LOG"

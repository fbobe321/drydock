#!/usr/bin/env bash
# control_noratchet.sh — SINGLE-ATTEMPT baseline (NO ratchet) for ONE task.
# Drives the model once through the real TUI (same instruction+research prompt as the
# ratchet's round 1) and scores with the real checker — but with NO cumulative selection,
# NO snapshot, NO rollback, NO continuation. This measures how many the model+harness solve
# on their own, so (ratchet solves − control solves) = the ratchet's actual marginal value.
set -u
ROOT=/data3/tbench_local
export TASKS="$ROOT/tasks/terminal-bench-2"
export LLM_URL="${LLM_URL:-http://host.docker.internal:8000/v1}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
source "$ROOT/tui_task_lib.sh"
PY=/home/bobef/miniconda3/bin/python3
task="${1:?task}"; BUDGET="${2:-1200}"
OUT=/data3/tbench_local/frontier/selfdistill/control; mkdir -p "$OUT"
LOG="$OUT/${task}.log"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
_ctr(){ echo "ddt_$1"; }

score(){   # passing/total from the real checker; strips /tests after
  local ctr="$1"
  docker exec "$ctr" mkdir -p /tests /logs/verifier >/dev/null 2>&1
  docker cp "$TASKS/$task/tests/." "$ctr:/tests/" >/dev/null 2>&1
  timeout 340 docker exec "$ctr" bash /tests/test.sh >/dev/null 2>&1
  docker exec "$ctr" cat /logs/verifier/ctrf.json 2>/dev/null | "$PY" -c "
import sys,json
try:
  s=json.load(sys.stdin)['results']['summary']; print(s.get('passed',0), s.get('tests',0))
except Exception: print(0,0)"
  docker exec "$ctr" rm -rf /tests /logs >/dev/null 2>&1
}

drive(){   # ONE turn through the real TUI (identical to ratchet round 1)
  local ctr sess pane st start; ctr=$(_ctr "$task"); sess="ddt_$task"
  printf 'trajectory_file = "/app/.dd_trajectory.json"\nstall_retry_secs = 300\n' \
    | docker exec -i "$ctr" bash -c 'mkdir -p ~/.drydock && cat > ~/.drydock/config.toml' 2>/dev/null
  ddt_tui "$task" >/dev/null 2>&1; sleep 10
  tmux send-keys -t "$sess" -- "$1"; tmux send-keys -t "$sess" Enter
  start=$(date +%s)
  while true; do
    sleep 20
    pane=$(tmux capture-pane -t "$sess" -p 2>/dev/null)
    st=$(echo "$pane" | grep -oE "⚓ (working|ready)|Approve" | tail -1)
    echo "$st" | grep -q Approve && tmux send-keys -t "$sess" "a"
    echo "$st" | grep -q ready && break
    [ $(( $(date +%s)-start )) -gt "$BUDGET" ] && break
  done
}

say "===== CONTROL (no ratchet) $task ====="
instr="$(cat "$TASKS/$task/instruction.md")"
RESEARCH="
Approach: investigate the actual files/errors first, research the technique empirically, verify with the task's own checker. Do not guess."
ddt_up "$task" >/dev/null 2>&1 || { say "$task: up FAILED"; echo "$task,ERR,," >> "$OUT/control_results.csv"; exit 1; }
drive "$instr
$RESEARCH"
read -r ps pt < <(score "$(_ctr "$task")")
solved=0; [ "${pt:-0}" -gt 0 ] && [ "${ps:-0}" -ge "${pt:-0}" ] && solved=1
say "RESULT $task solved=$solved ${ps:-0}/${pt:-0}"
ddt_down "$task" >/dev/null 2>&1

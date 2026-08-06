#!/usr/bin/env bash
# ratchet_solve.sh — cumulative-selection ("ratchet") solver for ONE task.
#
# The Dawkins ratchet applied to self-distillation collection: the current best-of-N uses a FRESH
# container per attempt (full backward slip every time — no pawl), which is why the base can't crack
# tasks needing more than one attempt's worth of work. Here we PERSIST verified progress across rounds:
#   - fitness = # of the REAL checker's tests passing (CTRF json — a graded signal, not binary)
#   - PAWL: snapshot (docker commit) the container whenever the pass-count IMPROVES (lock in forward)
#   - block backslip: if a round doesn't improve, discard it and restore the best snapshot next round
# Same base model, NO stronger teacher — just a ratchet instead of one-shot attempts.
#
# Anti-cheat: the checker is copied in ONLY to score, then /tests+/logs are DELETED before the snapshot,
# so the persisted state the model sees never contains the tests. The model advances on its own
# understanding; we measure with the hidden verifier (a legitimate reward signal, like RL).
#
# Usage: ratchet_solve.sh <task> [MAX_ROUNDS] [ROUND_BUDGET_S]   (assumes the LLM server is up)
set -u
ROOT=/data3/tbench_local
export TASKS="$ROOT/tasks/terminal-bench-2"
export DD_VER="${DD_VER:-3.0.138}"
export LLM_URL="${LLM_URL:-http://host.docker.internal:8000/v1}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
source "$ROOT/tui_task_lib.sh"
task="${1:?task}"; MAX_ROUNDS="${2:-6}"; ROUND_BUDGET="${3:-1200}"
OUT=/data3/tbench_local/frontier/selfdistill/ratchet; mkdir -p "$OUT"
LOG="$OUT/${task}.log"; SNAP="ratchet_${task}:best"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
_ctr(){ echo "ddt_$1"; }

# fitness = passing test count from the real checker; leaves the container CLEAN (no /tests) after.
score(){
  local ctr="$1"
  docker exec "$ctr" mkdir -p /tests /logs/verifier >/dev/null 2>&1
  docker cp "$TASKS/$task/tests/." "$ctr:/tests/" >/dev/null 2>&1
  timeout 340 docker exec "$ctr" bash /tests/test.sh >/dev/null 2>&1
  local res; res=$(docker exec "$ctr" cat /logs/verifier/ctrf.json 2>/dev/null | /home/bobef/miniconda3/bin/python3 -c "
import sys,json
try:
  s=json.load(sys.stdin)['results']['summary']; print(s.get('passed',0), s.get('tests',0))
except Exception: print(0,0)
")
  docker exec "$ctr" rm -rf /tests /logs >/dev/null 2>&1   # anti-cheat: strip the checker from persisted state
  echo "$res"
}

restore_snapshot(){   # bring ddt_<task> up from the best snapshot (has drydock + accumulated /app progress)
  local ctr wd; ctr=$(_ctr "$task")
  docker rm -f "$ctr" >/dev/null 2>&1
  wd=$(docker image inspect "$SNAP" --format '{{.Config.WorkingDir}}' 2>/dev/null); [ -n "$wd" ] || wd=/app
  docker run -d --name "$ctr" --add-host=host.docker.internal:host-gateway -w "$wd" "$SNAP" sleep infinity >/dev/null 2>&1
}

drive(){   # one round through the real TUI
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
    [ $(( $(date +%s)-start )) -gt "$ROUND_BUDGET" ] && break
  done
}

say "===== RATCHET $task (max_rounds=$MAX_ROUNDS budget=${ROUND_BUDGET}s) ====="
docker rmi "$SNAP" >/dev/null 2>&1
best=-1; total=0; solved=0
instr="$(cat "$TASKS/$task/instruction.md")"
RESEARCH="
Approach: investigate the actual files/errors first (read source, run things, inspect state) before editing; research the specific technique/library/error empirically and verify. Do not guess."
for r in $(seq 1 "$MAX_ROUNDS"); do
  if [ "$r" = 1 ]; then
    ddt_up "$task" >/dev/null 2>&1 || { say "round 1 up FAILED"; break; }
    prompt="$instr
$RESEARCH"
  else
    restore_snapshot || { say "restore FAILED"; break; }
    prompt="$instr
$RESEARCH

[CONTINUATION — your previous work is PRESERVED in this container.] A hidden automated checker currently passes ${best}/${total} of its checks. First inspect the CURRENT state (what already works vs what's still broken), then advance: make MORE checks pass WITHOUT breaking what already passes. Do not restart from scratch."
  fi
  say "round $r: driving model (from ${best}/${total})…"
  drive "$prompt"
  read -r ps pt < <(score "$(_ctr "$task")")
  say "round $r: checker = $ps/$pt"
  if [ "${pt:-0}" -gt 0 ] && [ "${ps:-0}" -ge "$pt" ]; then
    say "🎉 SOLVED $task at round $r ($ps/$pt) — the ratchet cracked a task the base can't one-shot"
    docker cp "$(_ctr "$task"):/app/.dd_trajectory.json" "$OUT/${task}_solved_traj.json" >/dev/null 2>&1
    solved=1; best=$ps; total=$pt; break
  fi
  if [ "${ps:-0}" -gt "$best" ]; then
    docker commit "$(_ctr "$task")" "$SNAP" >/dev/null 2>&1
    best=$ps; total=$pt; say "round $r: PAWL — locked in $ps/$pt"
  else
    say "round $r: no gain ($ps ≤ $best) — discard, rollback to best next round"
  fi
  ddt_down "$task" >/dev/null 2>&1
  # early-abort: no gradient for the pawl to grab (best still 0 after N rounds) → stop wasting rounds.
  if [ "${ABORT_FLAT_ROUNDS:-0}" -gt 0 ] && [ "$best" -le 0 ] && [ "$r" -ge "$ABORT_FLAT_ROUNDS" ]; then
    say "round $r: still 0/$total after $r rounds — no gradient, aborting early (ABORT_FLAT_ROUNDS=$ABORT_FLAT_ROUNDS)"
    break
  fi
done
say "===== RATCHET $task DONE: solved=$solved best=$best/$total ====="
echo "$task,$solved,$best,$total" >> "$OUT/ratchet_results.csv"
ddt_down "$task" >/dev/null 2>&1; docker rmi "$SNAP" >/dev/null 2>&1

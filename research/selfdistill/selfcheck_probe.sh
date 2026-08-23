#!/usr/bin/env bash
# selfcheck_probe.sh <task> — can the agent write its OWN scorecard, and does it agree
# with ground truth?
#
# WHY: the ratchet needs a fitness signal. On a benchmark that is the hidden checker; on
# everyday work there is none, so the agent must compile the stated goal into an
# executable check. That generalization is currently an ASSERTION. tbench has ground
# truth, so it can measure it: have the agent author a check from instruction.md alone,
# then score the same solution with BOTH the agent's check and the official checker.
#
# THE NUMBER THAT MATTERS is the FALSE-PASS rate (self says PASS, official says FAIL).
# That is exactly how much a self-authored target can be trusted as a search objective —
# a high false-pass rate means search against it would optimize into confident wrongness.
#
# THREE VALIDITY GUARDS:
#   1. NO LEAKAGE  — the check is authored from instruction.md only; /tests is never
#      mounted during authoring (ddt_up does not provide it; we copy tests in only to
#      score, then delete them).
#   2. FROZEN + ORDERED — the check is written BEFORE any solution exists and copied OUT
#      to the host immediately, so the solve phase cannot reverse-engineer or edit it to
#      fit. Solving happens in a FRESH container.
#   3. SAME SOLVER — the solve phase uses the identical single-attempt prompt as
#      control_noratchet.sh, so self-check quality is measured, not a different solver.
set -u
ROOT=/data3/tbench_local
export TASKS="$ROOT/tasks/terminal-bench-2"
export LLM_URL="${LLM_URL:-http://192.168.50.20:8000/v1}"
export DD_VER="${DD_VER:-3.1.15}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
source "$ROOT/tui_task_lib.sh"
PY=/home/bobef/miniconda3/bin/python3
task="${1:?task}"; BUDGET="${2:-900}"
SD="$(cd "$(dirname "$0")" && pwd)"
OUT="$SD/selfcheck"; mkdir -p "$OUT/checks"
CSV="$OUT/selfcheck_results.csv"
LOG="$OUT/${task}.log"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
_ctr(){ echo "ddt_$1"; }
[ -f "$CSV" ] || echo "task,self_pass,true_pass,true_passed,true_total,check_bytes" > "$CSV"

drive(){   # one turn through the real TUI (identical wait logic to control_noratchet.sh)
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

true_score(){   # official checker -> "<passed> <total>"; strips /tests after
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

instr="$(cat "$TASKS/$task/instruction.md")"
say "===== SELFCHECK $task ====="

# ── PHASE A: author the check, WITHOUT solving ───────────────────────────────
ddt_up "$task" >/dev/null 2>&1 || { say "up FAILED (A)"; exit 1; }
drive "Below is a task specification. Do NOT solve it. Your ONLY job right now is to write a verification script at /app/selfcheck.sh that decides whether the task has been completed correctly.

Requirements for /app/selfcheck.sh:
- exit 0 if and only if the task is correctly complete; non-zero otherwise
- check the OBSERVABLE result (files produced, their format/content, program behaviour) — not the presence of source code
- be concrete: assert specific filenames, formats, counts, values implied by the spec
- do not require network access; do not modify the solution

TASK SPECIFICATION:
$instr"
docker cp "$(_ctr "$task"):/app/selfcheck.sh" "$OUT/checks/${task}.sh" >/dev/null 2>&1
bytes=$(stat -c %s "$OUT/checks/${task}.sh" 2>/dev/null || echo 0)
ddt_down "$task" >/dev/null 2>&1
[ "${bytes:-0}" -lt 20 ] && { say "no usable check authored (bytes=$bytes)"; echo "$task,NA,NA,,,$bytes" >> "$CSV"; exit 0; }
say "authored check: ${bytes}B (frozen on host)"

# ── PHASE B: solve in a FRESH container (identical prompt to control_noratchet) ──
ddt_up "$task" >/dev/null 2>&1 || { say "up FAILED (B)"; exit 1; }
drive "$instr
Approach: investigate the actual files/errors first, research the technique empirically, verify with the task's own checker. Do not guess."

# ── PHASE C: score with BOTH the frozen self-check and the official checker ───
docker cp "$OUT/checks/${task}.sh" "$(_ctr "$task"):/tmp/selfcheck.sh" >/dev/null 2>&1
timeout 300 docker exec "$(_ctr "$task")" bash /tmp/selfcheck.sh >/dev/null 2>&1 && self_pass=1 || self_pass=0
read -r tp tt < <(true_score "$(_ctr "$task")")
true_pass=0; [ "${tt:-0}" -gt 0 ] && [ "${tp:-0}" -ge "${tt:-0}" ] && true_pass=1
echo "$task,$self_pass,$true_pass,${tp:-0},${tt:-0},$bytes" >> "$CSV"
verdict=agree; [ "$self_pass" != "$true_pass" ] && verdict=DISAGREE
[ "$self_pass" = 1 ] && [ "$true_pass" = 0 ] && verdict="FALSE-PASS (the dangerous one)"
say "RESULT $task self=$self_pass true=$true_pass (${tp:-0}/${tt:-0}) -> $verdict"
ddt_down "$task" >/dev/null 2>&1

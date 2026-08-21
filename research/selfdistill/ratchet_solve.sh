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
export DD_VER="${DD_VER:-3.1.15}"
export LLM_URL="${LLM_URL:-http://host.docker.internal:8000/v1}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
source "$ROOT/tui_task_lib.sh"
task="${1:?task}"; MAX_ROUNDS="${2:-6}"; ROUND_BUDGET="${3:-1200}"
OUT=/data3/tbench_local/frontier/selfdistill/ratchet; mkdir -p "$OUT"
LOG="$OUT/${task}.log"; SNAP="ratchet_${task}:best"
SD="$(cd "$(dirname "$0")" && pwd)"; PY=/home/bobef/miniconda3/bin/python3
# FINE_FITNESS=1 (default): pawl on the lexicographic (passed, partial) signal so a
# within-plateau sub-goal gain (a failing check going crash→runs-but-wrong) is locked
# in. =0 restores the original strict pass-count pawl for a controlled comparison.
FINE_FITNESS="${FINE_FITNESS:-1}"
# DIVERSIFY_ON_STALL=1 (default): when a round makes NO lex-fitness gain, the next round
# is driven from the SAME best snapshot but with a "your last approach reproduced the same
# result — try a MATERIALLY different one" nudge instead of the plain "continue". This is
# the plain-ratchet answer to the observed flatline (model re-derives the identical wrong
# result every round). =0 keeps the plain continuation nudge for a controlled A/B.
DIVERSIFY_ON_STALL="${DIVERSIFY_ON_STALL:-1}"
# FRESH_RESTART_ON_STALL=1: stronger escalation for the flatline — when stalled AND there is
# NO progress to preserve (best<=0 checks pass), the restored snapshot is just the prior WRONG
# attempt and primes the model to repeat it. Abandon it: bring up a CLEAN task container and
# re-attempt with a "reconsider the approach" hint. Only fires at best<=0 (multi-check tasks
# with real progress keep their snapshot via diversify). =0 (default) keeps snapshot behavior.
FRESH_RESTART_ON_STALL="${FRESH_RESTART_ON_STALL:-0}"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
_ctr(){ echo "ddt_$1"; }

# fitness = (passed, tests, partial) from the real checker via ctrf_fitness.py — the
# partial term gives the pawl a within-plateau gradient (see ctrf_fitness.py). Leaves
# the container CLEAN (no /tests) after.
score(){
  local ctr="$1"
  docker exec "$ctr" mkdir -p /tests /logs/verifier >/dev/null 2>&1
  docker cp "$TASKS/$task/tests/." "$ctr:/tests/" >/dev/null 2>&1
  timeout 340 docker exec "$ctr" bash /tests/test.sh >/dev/null 2>&1
  local res; res=$(docker exec "$ctr" cat /logs/verifier/ctrf.json 2>/dev/null | "$PY" "$SD/ctrf_fitness.py")
  docker exec "$ctr" rm -rf /tests /logs >/dev/null 2>&1   # anti-cheat: strip the checker from persisted state
  echo "${res:-0 0 0}"
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
best=-1; total=0; solved=0; bestpf=-1; stall=0
instr="$(cat "$TASKS/$task/instruction.md")"
# "More information" lever: append an optional per-task knowledge reference
# (augment/<task>.md) — domain facts the offline agent can't look up, NOT the
# solution. Leaves the task's tests/environment untouched, so the benchmark check
# is unchanged; only the agent's context gains reference material.
if [ -f "$SD/augment/$task.md" ]; then
  instr="$instr

--- REFERENCE (background domain knowledge — general facts, NOT the solution to this task) ---
$(cat "$SD/augment/$task.md")"
fi
RESEARCH="
Approach: investigate the actual files/errors first (read source, run things, inspect state) before editing; research the specific technique/library/error empirically and verify. Do not guess."
for r in $(seq 1 "$MAX_ROUNDS"); do
  if [ "$r" = 1 ]; then
    ddt_up "$task" >/dev/null 2>&1 || { say "round 1 up FAILED"; break; }
    prompt="$instr
$RESEARCH"
  elif [ "$FRESH_RESTART_ON_STALL" = 1 ] && [ "${stall:-0}" -ge 1 ] && [ "$best" -le 0 ]; then
    # FRESH-RESTART-ON-STALL: stalled with 0 checks passing → the snapshot is just the prior
    # WRONG attempt and anchors the model to repeat it. Start CLEAN instead (like round 1),
    # with a reconsider-the-approach hint and no "previous work preserved" framing.
    ddt_down "$task" >/dev/null 2>&1
    ddt_up "$task" >/dev/null 2>&1 || { say "round $r fresh-restart up FAILED"; break; }
    say "round $r: stalled ${stall}× at 0/${total} — FRESH RESTART (clean container, no snapshot anchor)"
    prompt="$instr
$RESEARCH

[A previous attempt using a straightforward approach did NOT pass the hidden checks. Before writing any code, reconsider the APPROACH from scratch: re-read the exact requirements and the expected OUTPUT (format, file path, edge cases), and choose a method likely to satisfy them. Confirm you actually PRODUCE the required output and check it against a concrete example before finishing."
  else
    restore_snapshot || { say "restore FAILED"; break; }
    if [ "$DIVERSIFY_ON_STALL" = 1 ] && [ "${stall:-0}" -ge 1 ]; then
      # DIVERSIFY-ON-STALL: the previous round(s) reproduced the SAME result from this exact
      # snapshot, so re-driving "continue" just repeats it. Force a different hypothesis —
      # and explicitly cover both flatline sub-modes: wrong algorithm (ran-but-wrong) and
      # missing/mis-placed output artifact (crashed-early / never produced the file).
      say "round $r: stalled ${stall}× — DIVERSIFYING approach (not repeating the last one)"
      prompt="$instr
$RESEARCH

[CONTINUATION — your previous work is PRESERVED; ${best}/${total} checks pass.] Your last attempt made NO new progress: re-running your previous approach yields the SAME result, so DO NOT repeat it. For the checks still FAILING, form a DIFFERENT hypothesis about the root cause. Re-read the exact failing behavior/error, then question your earlier assumptions: is the ALGORITHM/method wrong, the OUTPUT FORMAT or FILE PATH wrong, or an EDGE CASE unhandled? Confirm you actually PRODUCE the required output — right file, right location, right format — and check it against a concrete example before declaring done. Try a MATERIALLY different approach for the failing parts while keeping the checks that already pass."
    else
      prompt="$instr
$RESEARCH

[CONTINUATION — your previous work is PRESERVED in this container.] A hidden automated checker currently passes ${best}/${total} of its checks. First inspect the CURRENT state (what already works vs what's still broken), then advance: make MORE checks pass WITHOUT breaking what already passes. Do not restart from scratch."
    fi
  fi
  say "round $r: driving model (from ${best}/${total})…"
  drive "$prompt"
  read -r ps pt pf < <(score "$(_ctr "$task")"); pf=${pf:-0}
  say "round $r: checker = $ps/$pt (partial=$pf)"
  # ── CONTRASTIVE CAPTURE (2026-08-21) — see ratchet_evolve.sh for the rationale.
  # Successive rounds continue from the SAME pawl-held snapshot, so their transcripts
  # are comparable attempts with different rewards. Captured for preference pairs;
  # the winner-only _solved_traj.json cannot supply that signal.
  if [ "${CAPTURE_ROUNDS:-1}" = 1 ]; then
    _cd="$OUT/capture/$task"; mkdir -p "$_cd" 2>/dev/null
    if docker cp "$(_ctr "$task"):/app/.dd_trajectory.json" "$_cd/r${r}.json" >/dev/null 2>&1; then
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$task" "$r" "1" "plain" "pawl${best:-0}" "${ps:-0}" "${pt:-0}" \
        >> "$OUT/capture/manifest.tsv"
    fi
  fi
  if [ "${pt:-0}" -gt 0 ] && [ "${ps:-0}" -ge "$pt" ]; then
    say "🎉 SOLVED $task at round $r ($ps/$pt) — the ratchet cracked a task the base can't one-shot"
    docker cp "$(_ctr "$task"):/app/.dd_trajectory.json" "$OUT/${task}_solved_traj.json" >/dev/null 2>&1
    # MULTIPLE-SOLVES-PER-TASK: also archive this winning run as a VARIANT (unique name) so
    # re-solving a task accumulates diverse solutions for the heredity corpus (3-5x scale from
    # the tasks we already crack). Content-hashed so identical solutions dedupe, distinct accrue.
    mkdir -p "$OUT/variants" 2>/dev/null
    _vh=$(md5sum "$OUT/${task}_solved_traj.json" 2>/dev/null | cut -c1-8)
    [ -n "$_vh" ] && cp -n "$OUT/${task}_solved_traj.json" "$OUT/variants/${task}__${_vh}.json" 2>/dev/null
    solved=1; best=$ps; total=$pt; bestpf=1000; break
  fi
  # PAWL: lock in a strict pass increase, OR (finer fitness) a within-plateau sub-goal
  # gain — same passes but a failing check moved crash→runs-but-wrong (partial up).
  gain=0
  if [ "${ps:-0}" -gt "$best" ]; then
    gain=1
  elif [ "$FINE_FITNESS" = 1 ] && [ "${ps:-0}" -eq "$best" ] && [ "${pf:-0}" -gt "$bestpf" ]; then
    gain=1
  fi
  if [ "$gain" = 1 ]; then
    docker commit "$(_ctr "$task")" "$SNAP" >/dev/null 2>&1
    best=$ps; total=$pt; bestpf=$pf; stall=0
    say "round $r: PAWL — locked in $ps/$pt (partial=$pf)"
  else
    stall=$((stall+1))
    say "round $r: no gain ($ps/$pt partial=$pf ≤ best $best/$total partial=$bestpf) — rollback (stall=$stall)"
  fi
  ddt_down "$task" >/dev/null 2>&1
  # early-abort: no gradient for the pawl to grab → stop wasting rounds. With finer fitness
  # "no gradient" = no lexicographic gain for ABORT_FLAT_ROUNDS straight rounds (so a task
  # still climbing partial isn't killed); without it, the original "never passed anything".
  if [ "${ABORT_FLAT_ROUNDS:-0}" -gt 0 ] && [ "$solved" = 0 ]; then
    if [ "$FINE_FITNESS" = 1 ]; then
      [ "$stall" -ge "$ABORT_FLAT_ROUNDS" ] && { say "round $r: no lex-fitness gain for $stall rounds — aborting early"; break; }
    elif [ "$best" -le 0 ] && [ "$r" -ge "$ABORT_FLAT_ROUNDS" ]; then
      say "round $r: still 0/$total after $r rounds — no gradient, aborting early"; break
    fi
  fi
done
say "===== RATCHET $task DONE: solved=$solved best=$best/$total partial=$bestpf ====="
echo "$task,$solved,$best,$total,$bestpf" >> "$OUT/ratchet_results.csv"
ddt_down "$task" >/dev/null 2>&1; docker rmi "$SNAP" >/dev/null 2>&1

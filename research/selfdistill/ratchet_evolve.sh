#!/usr/bin/env bash
# ratchet_evolve.sh — EVOLUTIONARY ratchet for ONE task (framework recs 1-4,6 wired live).
# Superset of ratchet_solve.sh: instead of a single incumbent it keeps a Quality-Diversity
# ARCHIVE of snapshots (one elite per behaviour niche), escalates the VARIATION operator on
# stall (exploit→diversify→fan-out(λ)→crossover→restart), and CROSSES OVER complementary
# partial solutions by grafting a donor's solved sub-behaviour onto a base. All evolutionary
# decisions come from evolve_bridge.py (which uses the unit-tested drydock.ratchet core), so
# this script is just the docker/TUI glue.
#
#   ⚠️ LIVE-UNVALIDATED: the decision logic is unit-tested offline; this docker/TUI wiring needs
#   one GPU smoke run (deferred so the 2-stream pool sweep keeps the GPU). Opt-in; nothing else calls it.
#
# Usage: ratchet_evolve.sh <task> [MAX_ROUNDS] [ROUND_BUDGET_S]   (assumes the LLM server is up)
set -u
ROOT=/data3/tbench_local
export TASKS="$ROOT/tasks/terminal-bench-2"
export DRYDOCK_SRC="${DRYDOCK_SRC:-/data3/drydock-v3}"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
source "$ROOT/tui_task_lib.sh"
PY=/home/bobef/miniconda3/bin/python3
SD=/data3/tbench_local/frontier/selfdistill
task="${1:?task}"; ROUND_BUDGET="${3:-1200}"
# EFFORT (low|medium|high|xhigh|max) is the single dial: low = plain pawl, high+ = full
# evolutionary. It sets rounds + patience + fan-out + ladder (via evolve_bridge). $2 overrides rounds.
export EFFORT="${EFFORT:-high}"
MAX_ROUNDS="${2:-$("$PY" -c "import os,sys;sys.path.insert(0,os.environ['DRYDOCK_SRC'])
from drydock.ratchet import effort_profile;print(effort_profile('$EFFORT')['max_rounds'])" 2>/dev/null || echo 8)}"
OUT="$SD/ratchet"; mkdir -p "$OUT"
LOG="$OUT/${task}.evolve.log"; STATE="$OUT/${task}.evolve.state.json"; rm -f "$STATE"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
_ctr(){ echo "ddt_$1"; }

# fitness + behaviour descriptor from the REAL checker; strips /tests after (anti-cheat).
score(){   # echoes: "<passed> <total> <descriptor-json>"
  local ctr="$1"
  docker exec "$ctr" mkdir -p /tests /logs/verifier >/dev/null 2>&1
  docker cp "$TASKS/$task/tests/." "$ctr:/tests/" >/dev/null 2>&1
  timeout 340 docker exec "$ctr" bash /tests/test.sh >/dev/null 2>&1
  docker exec "$ctr" cat /logs/verifier/ctrf.json 2>/dev/null | "$PY" -c "
import sys,json
try:
  d=json.load(sys.stdin)['results']; s=d['summary']
  names=[t.get('name') for t in d.get('tests',[]) if t.get('status')=='passed']
  print(s.get('passed',0), s.get('tests',0), json.dumps(names))
except Exception: print(0,0,'null')
"
  docker exec "$ctr" rm -rf /tests /logs >/dev/null 2>&1
}

commit_ref(){ local g="$1"; local ref="ratchet_${task}_g${g}:e"; docker commit "$(_ctr "$task")" "$ref" >/dev/null 2>&1 && echo "$ref"; }
restore_ref(){   # bring ddt_<task> up from a snapshot ref
  local ref="$1" ctr wd; ctr=$(_ctr "$task"); docker rm -f "$ctr" >/dev/null 2>&1
  wd=$(docker image inspect "$ref" --format '{{.Config.WorkingDir}}' 2>/dev/null); [ -n "$wd" ] || wd=/app
  docker run -d --name "$ctr" --add-host=host.docker.internal:host-gateway -w "$wd" "$ref" sleep infinity >/dev/null 2>&1
}
graft_donor(){   # crossover: make the donor's solution visible under /app_donor for the model to fold in
  local donor="$1" ctr; ctr=$(_ctr "$task")
  local tmp="xover_${task}_$$"; docker create --name "$tmp" "$donor" >/dev/null 2>&1
  docker cp "$tmp:/app" "/tmp/$tmp" >/dev/null 2>&1 && docker cp "/tmp/$tmp" "$ctr:/app_donor" >/dev/null 2>&1
  docker rm -f "$tmp" >/dev/null 2>&1; rm -rf "/tmp/$tmp" >/dev/null 2>&1
}
render_prompt(){   # per-variant prompt from the tested core; maps spec mode -> diversify_prompt op
  local mode="$1" passed="$2" total="$3" dpop
  case "$mode" in rethink) dpop=diversify;; restart) dpop=restart;; *) dpop=continue;; esac
  "$PY" -c "
import os,sys; sys.path.insert(0,os.environ['DRYDOCK_SRC'])
from drydock.ratchet import diversify_prompt
print(diversify_prompt(open('$AUG_INSTR').read(), int('$passed'), int('$total'), '$dpop'))"
}

drive(){   # one turn through the real TUI with a given prompt
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

say "===== EVOLVE $task (max_rounds=$MAX_ROUNDS budget=${ROUND_BUDGET}s) ====="
instr="$(cat "$TASKS/$task/instruction.md")"
# "More information" lever (matches ratchet_solve.sh): append optional per-task
# domain reference; leaves tests/env untouched.
if [ -f "$SD/augment/$task.md" ]; then
  instr="$instr

--- REFERENCE (background domain knowledge — general facts, NOT the solution to this task) ---
$(cat "$SD/augment/$task.md")"
fi
# effective (possibly-augmented) instruction on disk so render_prompt's variants
# see the reference too, not just the research/crossover modes.
AUG_INSTR="$(mktemp)"; printf '%s' "$instr" > "$AUG_INSTR"; trap 'rm -f "$AUG_INSTR"' EXIT
RESEARCH="
Approach: investigate the actual files/errors first, research the technique empirically, verify with the task's own checker. Do not guess."
op="exploit"; params='{"variants":1}'; best_ref=""; solved=0; bp=-1; bt=0
XBASE=""; XDONOR=""; XWANTS=""   # crossover state (set by the bridge on the crossover rung)

for r in $(seq 1 "$MAX_ROUNDS"); do
  # ---- per-variant modes (ELITISM: variant 1 is ALWAYS the steady exploit move,
  #      so a round can never score below the plain pawl; extras only explore) ----
  if [ "$r" = 1 ]; then
    modes="research"                          # round 1: one research attempt, fresh container
    ddt_up "$task" >/dev/null 2>&1 || { say "round 1 up FAILED"; break; }
  else
    modes=$("$PY" -c "import json,sys; s=json.loads(sys.argv[1]).get('specs',[{'mode':'continue'}]); print(' '.join(x['mode'] for x in s))" "$params" 2>/dev/null || echo continue)
  fi
  nvar=$(echo $modes | wc -w)

  # ---- run the round; keep the best variant (exploit-first guarantees ≥ plain ratchet) ----
  best_line=""; best_p=-1; v=0
  for mode in $modes; do
    v=$((v+1))
    if [ "$mode" = "crossover" ]; then
      restore_ref "$XBASE" && graft_donor "$XDONOR"
      say "round $r: crossover variant — base=$XBASE donor=$XDONOR wants=$XWANTS"
    elif [ "$r" != 1 ]; then
      restore_ref "$best_ref" || { say "restore FAILED"; break; }
    fi
    case "$mode" in
      research) vp="$instr
$RESEARCH" ;;
      crossover) vp="$instr
$RESEARCH

[CROSSOVER] A verifier passes $bp/$bt. A DIFFERENT working solution is mounted read-only at /app_donor — it already passes checks yours misses ($XWANTS). Inspect /app_donor, fold ITS approach for those checks into /app WITHOUT breaking what already passes." ;;
      *) vp="$(render_prompt "$mode" "$bp" "$bt")" ;;
    esac
    drive "$vp"
    read -r ps pt desc < <(score "$(_ctr "$task")")
    say "round $r op=$op variant=$v/$nvar mode=$mode: $ps/$pt"
    # ── CONTRASTIVE CAPTURE (2026-08-21) ────────────────────────────────────
    # Every variant in a round starts from the SAME base_ref but scores differently,
    # so these are exactly (better, worse) PREFERENCE pairs. The winner-only corpus
    # (_evolved_traj.json = the solving round only) structurally cannot provide this
    # — that gap is why the MoE->31B SFT run had nothing ratchet-like to learn.
    # A pair needs only "one attempt beat another", NOT a solve, so this yields
    # signal on tasks the model FAILS (where the headroom actually is).
    if [ "${CAPTURE_ROUNDS:-1}" = 1 ]; then
      _cd="$OUT/capture/$task"; mkdir -p "$_cd" 2>/dev/null
      if docker cp "$(_ctr "$task"):/app/.dd_trajectory.json" "$_cd/r${r}_v${v}.json" >/dev/null 2>&1; then
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$task" "$r" "$v" "$mode" "${best_ref:-none}" "${ps:-0}" "${pt:-0}" \
          >> "$OUT/capture/manifest.tsv"
      fi
    fi
    if [ "${ps:-0}" -gt "$best_p" ]; then best_p="$ps"; best_line="$ps $pt $desc"; fi
  done
  read -r ps pt desc <<< "$best_line"

  # ---- snapshot this round + let the bridge decide the next move ----
  ref=$(commit_ref "$r"); [ -z "$ref" ] && ref="none"
  echo "{\"id\":\"g$r\",\"passed\":${ps:-0},\"total\":${pt:-0},\"descriptor\":${desc:-null},\"snapshot\":\"$ref\",\"cost\":$r}" > "$OUT/${task}.payload.json"
  dec=$("$PY" "$SD/evolve_bridge.py" record "$STATE" "$OUT/${task}.payload.json")
  op=$(echo "$dec"      | "$PY" -c "import json,sys;print(json.load(sys.stdin)['operator'])")
  params=$(echo "$dec"  | "$PY" -c "import json,sys;print(json.dumps(json.load(sys.stdin)['params']))")
  best_ref=$(echo "$dec"| "$PY" -c "import json,sys;print(json.load(sys.stdin)['best_ref'] or '')")
  bp=$(echo "$dec"      | "$PY" -c "import json,sys;print(json.load(sys.stdin)['best_passed'])")
  bt=$(echo "$dec"      | "$PY" -c "import json,sys;print(json.load(sys.stdin)['best_total'])")
  niches=$(echo "$dec"  | "$PY" -c "import json,sys;print(json.load(sys.stdin)['niches'])")
  say "round $r: best=$bp/$bt niches=$niches next-op=$op"
  if [ "$(echo "$dec" | "$PY" -c "import json,sys;print(json.load(sys.stdin)['solved'])")" = "True" ]; then
    solved=1; say "🎉 SOLVED $task at round $r ($bp/$bt) via evolutionary ratchet"
    docker cp "$(_ctr "$task"):/app/.dd_trajectory.json" "$OUT/${task}_evolved_traj.json" >/dev/null 2>&1; break
  fi
  if [ "$op" = "crossover" ]; then
    XBASE=$(echo "$dec" | "$PY" -c "import json,sys;x=json.load(sys.stdin)['crossover'];print(x['base'] if x else '')")
    XDONOR=$(echo "$dec"| "$PY" -c "import json,sys;x=json.load(sys.stdin)['crossover'];print(x['donor'] if x else '')")
    XWANTS=$(echo "$dec"| "$PY" -c "import json,sys;x=json.load(sys.stdin)['crossover'];print(','.join(x['wants']) if x else '')")
  fi
  ddt_down "$task" >/dev/null 2>&1
done

say "===== EVOLVE $task DONE: solved=$solved best=$bp/$bt ====="
echo "$task,$solved,$bp,$bt" >> "$OUT/ratchet_evolve_results.csv"
ddt_down "$task" >/dev/null 2>&1
# cleanup this task's snapshot images
for img in $(docker images --format '{{.Repository}}:{{.Tag}}' | grep "^ratchet_${task}_g"); do docker rmi "$img" >/dev/null 2>&1; done

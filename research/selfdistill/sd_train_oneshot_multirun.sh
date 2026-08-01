#!/usr/bin/env bash
# sd_train_oneshot_multirun.sh — FORK 1: pin the TRUE multi-task reproduction rate.
# Single-run evals gave composition r=128 = 2/7 and pruned comp2 = 0/7 (within-noise?). Serve the
# unpruned r=128 composition adapter (the one with signal) on the MATCHED base and run each of the 7
# trained tasks 3x. If break-filter/custom are 3/3 the true rate is ~2/7 solid; if ~1/3 they were noise.
# No training — just serve + eval. Named sd_train_oneshot* so the watchdog defers; relaunches the loop
# on exit (-> P2 volume collection resumes for the rest of the night).
set -u
ROOT=/data3/tbench_local
SD="$ROOT/frontier/selfdistill"
MODELS=/data3/Models
CONTAINER=llamacpp-gemma4-31b
EVAL_CUTOFF="${EVAL_CUTOFF:-1200}"
HFQ=/data3/Models/gemma-4-31B-it-hf-Q5_K_M.gguf
LORA=/data3/Models/selfdistill-composition-lora.gguf   # unpruned r=128 (scored 2/7 single-run)
TRAINED=(break-filter-js-from-html custom-memory-heap-crash large-scale-text-editing constraints-scheduling bn-fit-modify distribution-search build-pmars)
RUNS="${RUNS:-3}"
LOG="$SD/multirun.log"; OUT="$SD/multirun_results.csv"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

serve(){
  local base="$1" lora="${2:-}" lora_arg=""
  docker rm -f "$CONTAINER" >/dev/null 2>&1; sleep 1
  [ -n "$lora" ] && lora_arg="--lora /models/$(basename "$lora")"
  docker run -d --name "$CONTAINER" --gpus all -p 8000:8000 -v "$MODELS":/models \
    ghcr.io/ggml-org/llama.cpp:server-cuda \
    -m "/models/$(basename "$base")" --mmproj /models/mmproj-gemma4-31b-F16.gguf \
    --host 0.0.0.0 --port 8000 -ngl 99 -c 32768 -np 1 --jinja \
    -ctk q8_0 -ctv q8_0 --alias gemma4 --reasoning-budget 20000 --tensor-split 1,1 \
    $lora_arg >/dev/null 2>&1
  local i
  for i in $(seq 1 90); do
    curl -sf http://localhost:8000/health >/dev/null 2>&1 && {
      say "server up ($(basename "$base") lora=$([ -n "$lora" ] && basename "$lora" || echo none))"
      [ -n "$lora" ] && say "  $(docker logs "$CONTAINER" 2>&1 | grep -iE 'lora adapter from' | head -1)"
      sleep 3; return 0; }
    sleep 5
  done
  say "server FAILED"; docker logs --tail 40 "$CONTAINER" 2>&1 | tail -40 >> "$LOG"; return 1
}

eval_task(){
  local task="$1" sess="ddt_$1" pane st start reward ctr
  source "$ROOT/tui_task_lib.sh"
  ddt_up "$task" >/dev/null 2>&1 || { echo 0; return; }
  ctr="ddt_$task"
  printf 'trajectory_file = "/app/.dd_trajectory.json"\nstall_retry_secs = 300\n' \
    | docker exec -i "$ctr" bash -c 'mkdir -p ~/.drydock && cat > ~/.drydock/config.toml' 2>/dev/null
  local prompt; prompt="$(cat "$ROOT/tasks/terminal-bench-2/$task/instruction.md")"
  ddt_tui "$task" >/dev/null 2>&1; sleep 10
  tmux send-keys -t "$sess" -- "$prompt"; tmux send-keys -t "$sess" Enter
  start=$(date +%s)
  while true; do
    sleep 20
    pane=$(tmux capture-pane -t "$sess" -p 2>/dev/null)
    st=$(echo "$pane" | grep -oE "⚓ (working|ready)|Approve" | tail -1)
    echo "$st" | grep -q Approve && tmux send-keys -t "$sess" "a"
    echo "$st" | grep -q ready && { reward=$(ddt_verify "$task" 2>&1 | grep -oE "reward: [01]" | grep -oE "[01]" | tail -1); break; }
    [ $(( $(date +%s)-start )) -gt "$EVAL_CUTOFF" ] && { reward=$(ddt_verify "$task" 2>&1 | grep -oE "reward: [01]" | grep -oE "[01]" | tail -1); reward="${reward:-0}"; break; }
  done
  ddt_down "$task" >/dev/null 2>&1
  echo "${reward:-0}"
}

say "===== MULTIRUN START — 7 trained tasks x $RUNS, unpruned r=128 adapter; stopping loop ====="
for p in $(pgrep -f 'selfdistill.sh loop' 2>/dev/null); do kill "$p" 2>/dev/null; done
pkill -f 'frontier_collect_n3.sh' 2>/dev/null; sleep 2
for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep '^ddt_'); do docker rm -f "$c" >/dev/null 2>&1; done

echo "task,run,reward" > "$OUT"
serve "$HFQ" "$LORA" || { say "serve failed — relaunching loop"; setsid nohup bash "$SD/selfdistill_launch.sh" >> "$SD/selfdistill.log" 2>&1 & exit 1; }
for t in "${TRAINED[@]}"; do
  for n in $(seq 1 "$RUNS"); do
    r=$(eval_task "$t"); say "  $t run$n -> reward=$r"; echo "$t,$n,$r" >> "$OUT"
  done
done

say "===== MULTIRUN DONE ====="
/home/bobef/miniconda3/bin/python3 - "$OUT" <<'PY' | tee -a "$LOG"
import sys,csv,collections
d=collections.defaultdict(list)
for row in csv.DictReader(open(sys.argv[1])): d[row["task"]].append(int(row["reward"]))
for t,rs in d.items(): print(f"  {t}: {sum(rs)}/{len(rs)}")
print("  TOTAL tasks with >=1 solve:", sum(1 for rs in d.values() if any(rs)), "/", len(d))
PY

docker rm -f "$CONTAINER" >/dev/null 2>&1
say "relaunching self-distill loop (P2 volume collection resumes)"
setsid nohup bash "$SD/selfdistill_launch.sh" >> "$SD/selfdistill.log" 2>&1 &
say "loop relaunched"

#!/usr/bin/env bash
# sd_train_oneshot_hardmemo.sh — DECISIVE test of the operator hypothesis:
#   "if the model can solve a task, we must be able to train it to do it."
#
# Overfit HARD on a SINGLE verified trace (distribution-search, deterministic,
# HF-base-plain=0) until loss->~0, serve on the MATCHED HF base, and check the
# model now reproduces the solve through the real TUI. Single-task keeps the
# memorization run ~25min (memorizing all 7 would be ~2-3h).
#   - reproduces (reward=1, esp. repeatably) -> hypothesis holds; loop just needs
#     matched base + real memorization (both fixable) -> scale.
#   - loss->0 but still reward=0 -> weight-space memorization does NOT manifest at
#     inference for agentic tasks: the deep finding.
#
# Named 'sd_train_oneshot*' so the self-distill watchdog DEFERS relaunch. Relaunches
# the loop on exit; watchdog relaunches within 15m if this crashes.
set -u
ROOT=/data3/tbench_local
SD="$ROOT/frontier/selfdistill"
MODELS=/data3/Models
MODEL_HF=/data3/Models/gemma-4-31B-it-hf
LLAMA=/data3/build/llama.cpp
PY_TRAIN=/data3/compass/.venv-train/bin/python
PYT=/home/bobef/miniconda3/bin/python3
CONTAINER=llamacpp-gemma4-31b
EVAL_CUTOFF="${EVAL_CUTOFF:-1200}"
HFQ=/data3/Models/gemma-4-31B-it-hf-Q5_K_M.gguf
SFT="$SD/gens/hardmemo/dist_sft.jsonl"
ADAPTER=/data3/compass/adapters/sd-hardmemo
LORA_GGUF=/data3/Models/selfdistill-hardmemo-lora.gguf
TASK=distribution-search
RUNS="${RUNS:-3}"
# hard-overfit knobs
STEPS="${STEPS:-300}"; SEQ="${SEQ:-512}"; ACCUM="${ACCUM:-1}"; LORA_R="${LORA_R:-32}"; LR="${LR:-0.0002}"
LOG="$SD/hardmemo.log"; OUT="$SD/hardmemo_results.csv"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

serve(){  # serve <base> [lora]
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
  say "server FAILED ($(basename "$base"))"; docker logs --tail 40 "$CONTAINER" 2>&1 | tail -40 >> "$LOG"; return 1
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

# ---------- borrow GPU ----------
say "===== HARDMEMO START — overfit $TASK (steps=$STEPS accum=$ACCUM r=$LORA_R lr=$LR); stopping loop ====="
for p in $(pgrep -f 'selfdistill.sh loop' 2>/dev/null); do kill "$p" 2>/dev/null; done
pkill -f 'frontier_collect_n3.sh' 2>/dev/null; sleep 2
for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep '^ddt_'); do docker rm -f "$c" >/dev/null 2>&1; done
say "stopping server + waiting for VRAM release"
docker stop "$CONTAINER" >/dev/null 2>&1; docker rm -f "$CONTAINER" >/dev/null 2>&1
t=0; while [ $t -lt 150 ]; do
  mx=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | sort -rn | head -1)
  [ "${mx:-99999}" -lt 1500 ] && { say "GPU freed (max ${mx}MiB)"; break; }
  sleep 5; t=$((t+5))
done

# ---------- train HARD ----------
say "training (overfit)…"
( cd /data3/compass && PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
    "$PY_TRAIN" -u -m compass train --model "$MODEL_HF" --data "$SFT" --mode sft --load-4bit \
    --steps "$STEPS" --seq "$SEQ" --accum "$ACCUM" --lora-r "$LORA_R" --lr "$LR" --out "$ADAPTER" ) >> "$LOG" 2>&1 \
  || { say "TRAIN FAILED — relaunching loop"; setsid nohup bash "$SD/selfdistill_launch.sh" >> "$SD/selfdistill.log" 2>&1 & exit 1; }
say "train done. loss curve:"; grep -E 'step [0-9]+/|last_loss|mean_last_10' "$LOG" | tail -8
PYTHONPATH="$LLAMA/gguf-py" "$PYT" "$LLAMA/convert_lora_to_gguf.py" "$ADAPTER" \
    --base "$MODEL_HF" --outfile "$LORA_GGUF" --outtype f16 >> "$LOG" 2>&1 \
  || { say "GGUF convert FAILED — relaunching loop"; setsid nohup bash "$SD/selfdistill_launch.sh" >> "$SD/selfdistill.log" 2>&1 & exit 1; }
say "adapter GGUF -> $LORA_GGUF ($(du -h "$LORA_GGUF"|cut -f1))"

# ---------- serve matched base + hardmemo LoRA, test reproduction ----------
echo "config,task,run,reward" > "$OUT"
serve "$HFQ" "$LORA_GGUF" || { say "serve failed"; setsid nohup bash "$SD/selfdistill_launch.sh" >> "$SD/selfdistill.log" 2>&1 & exit 1; }
for n in $(seq 1 "$RUNS"); do
  r=$(eval_task "$TASK"); say "  [HFbase+hardmemo] $TASK run$n -> reward=$r"; echo "HFbase+hardmemo,$TASK,$n,$r" >> "$OUT"
done

say "===== HARDMEMO DONE ====="; column -s, -t "$OUT" 2>/dev/null | tee -a "$LOG"

# ---------- restore ----------
docker rm -f "$CONTAINER" >/dev/null 2>&1
say "relaunching self-distill loop"
setsid nohup bash "$SD/selfdistill_launch.sh" >> "$SD/selfdistill.log" 2>&1 &
say "loop relaunched"

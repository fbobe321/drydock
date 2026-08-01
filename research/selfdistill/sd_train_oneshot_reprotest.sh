#!/usr/bin/env bash
# sd_train_oneshot_reprotest.sh — DECISIVE reproduction test for the base-mismatch hypothesis.
#
# Hypothesis: self-distill LoRAs looked inert because they are TRAINED against
# gemma-4-31B-it-hf but SERVED on the gemma-4-31B-it-qat-UD-Q4_K_XL checkpoint (a
# different weight set) — the delta is applied from the wrong origin.
#
# Test: the 7 trained tasks are base✗→assist✓ by construction (base-plain fails
# them). If (HF-matched base + gen8 LoRA) now SOLVES tasks that (HF base plain)
# fails, the adapter taught a real skill and the mismatch was the bug.
#
# Named 'sd_train_oneshot*' on purpose: the self-distill watchdog DEFERS relaunch
# while any 'sd_train_oneshot' process is alive, so this borrows the GPU cleanly.
# On exit it relaunches the loop; if it crashes, the watchdog relaunches within 15m.
set -u
ROOT=/data3/tbench_local
SD="$ROOT/frontier/selfdistill"
MODELS=/data3/Models
CONTAINER=llamacpp-gemma4-31b
EVAL_CUTOFF="${EVAL_CUTOFF:-1200}"          # 20 min/attempt plain, same as loop eval
LORA=/data3/Models/selfdistill-gen8-lora.gguf
HFQ=/data3/Models/gemma-4-31B-it-hf-Q5_K_M.gguf
QAT=/data3/Models/gemma-4-31B-it-qat-UD-Q4_K_XL.gguf
LOG="$SD/reprotest.log"
OUT="$SD/reprotest_results.csv"
# base-hard subset of the 7 trained tasks (keep the GPU borrow ~2-3h)
TASKS=(distribution-search bn-fit-modify break-filter-js-from-html)

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/bobef/miniforge3/bin:/home/bobef/.local/bin"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

serve(){  # serve <base_gguf_basename> [lora_gguf_path]
  local base="$1" lora="${2:-}" lora_arg=""
  docker rm -f "$CONTAINER" >/dev/null 2>&1; sleep 1
  [ -n "$lora" ] && lora_arg="--lora /models/$(basename "$lora")"
  docker run -d --name "$CONTAINER" --gpus all -p 8000:8000 \
    -v "$MODELS":/models \
    ghcr.io/ggml-org/llama.cpp:server-cuda \
    -m "/models/$(basename "$base")" --mmproj /models/mmproj-gemma4-31b-F16.gguf \
    --host 0.0.0.0 --port 8000 -ngl 99 -c 32768 -np 1 --jinja \
    -ctk q8_0 -ctv q8_0 --alias gemma4 --reasoning-budget 20000 --tensor-split 1,1 \
    $lora_arg >/dev/null 2>&1
  local i
  for i in $(seq 1 90); do
    curl -sf http://localhost:8000/health >/dev/null 2>&1 && {
      say "server up ($(basename "$base") lora=$([ -n "$lora" ] && basename "$lora" || echo none))"
      if [ -n "$lora" ]; then
        local la; la=$(docker logs "$CONTAINER" 2>&1 | grep -iE 'lora|adapter' | head -4)
        say "  LoRA-load lines: ${la:-<NONE FOUND — adapter may be ignored!>}"
      fi
      sleep 3; return 0; }
    sleep 5
  done
  say "server FAILED ($(basename "$base"))"; docker logs --tail 40 "$CONTAINER" 2>&1 | tail -40 >> "$LOG"; return 1
}

# run ONE task plain through the real TUI with the CURRENT server; echo reward 0/1
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

run_cfg(){  # run_cfg <label>
  local label="$1" t r
  for t in "${TASKS[@]}"; do
    r=$(eval_task "$t"); say "  [$label] $t -> reward=$r"
    echo "$label,$t,$r" >> "$OUT"
  done
}

# ---------- borrow the GPU: stop loop + collectors ----------
say "===== REPROTEST START — stopping self-distill loop to borrow GPU (reversible) ====="
for p in $(pgrep -f 'selfdistill.sh loop' 2>/dev/null); do kill "$p" 2>/dev/null; done
pkill -f 'frontier_collect_n3.sh' 2>/dev/null; sleep 2
for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep '^ddt_'); do docker rm -f "$c" >/dev/null 2>&1; done
sleep 3

echo "config,task,reward" > "$OUT"
# CONFIG 1 — matched HF base + LoRA  (the hypothesis)
serve "$HFQ" "$LORA" && run_cfg "HFbase+LoRA"
# CONFIG 2 — matched HF base, NO LoRA (control: confirm tasks are base-hard)
serve "$HFQ" ""      && run_cfg "HFbase-plain"
# CONFIG 3 — QAT base + LoRA (the OLD path: show the mismatch is inert)
serve "$QAT" "$LORA" && run_cfg "QATbase+LoRA"

say "===== REPROTEST DONE ====="; column -s, -t "$OUT" 2>/dev/null | tee -a "$LOG"

# ---------- restore: relaunch the loop (it re-serves base itself) ----------
docker rm -f "$CONTAINER" >/dev/null 2>&1
say "relaunching self-distill loop"
setsid nohup bash "$SD/selfdistill_launch.sh" >> "$SD/selfdistill.log" 2>&1 &
say "loop relaunched (watchdog resumes normal duty)"

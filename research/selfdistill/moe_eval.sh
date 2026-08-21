#!/usr/bin/env bash
# moe_eval.sh — A/B the MoE-trained adapter against base, on LEAKAGE-CLEAN task sets.
#
#   moe_eval.sh <n_generalization> <n_regression>
#
# WHY the arms (see moe_collect/README.md):
#   GENERALIZATION — tasks the 31B currently FAILS. A solve here = the easy MoE traces
#     taught something that transfers UP. This is the win condition.
#   REGRESSION — tasks the 31B currently PASSES, with the 14 trained-on tasks EXCLUDED
#     (evaluating on training tasks would measure memorization, not retention). A drop
#     here = we distilled the strong 31B DOWN toward a weak 4B-active model's style.
#
# Both arms run the SAME tasks against base and base+LoRA on the SAME matched HF-Q5
# checkpoint (a HF-trained LoRA is ~inert on the QAT weights, so QAT would show a
# spurious null). Single attempt per task via control_noratchet.sh — no ratchet, so
# this measures the model, not cumulative selection.
set -u
SD=/data3/tbench_local/frontier/selfdistill
OUT="$SD/moe_collect"
LOG="$OUT/eval.log"
CSV="$OUT/eval_results.csv"
MODELS=/data3/Models
CONTAINER=llamacpp-gemma4-31b
LORA="$MODELS/moe-easy-20260820-2147-lora.gguf"
BUDGET="${BUDGET:-900}"
NG="${1:-5}"; NR="${2:-5}"
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

[ -f "$LORA" ] || { say "ABORT: LoRA gguf missing: $LORA"; exit 1; }
[ -f "$CSV" ] || echo "arm,task,set,passed,total,solved" > "$CSV"

server_wait(){ local n="${1:-220}"; for i in $(seq 1 "$n"); do
  curl -sf http://localhost:8000/health >/dev/null 2>&1 && return 0; sleep 2; done; return 1; }

serve(){   # serve <base|lora>
  local lora_arg="" tag="base(HF-Q5)"
  if [ "$1" = lora ]; then lora_arg="--lora /models/$(basename "$LORA")"; tag="base+LoRA(HF-Q5)"; fi
  # the MoE generator server still owns :8000 on this box (collect is finished) —
  # free the port or the eval server can't bind. Restart it later for more collection.
  docker rm -f moe-smoke >/dev/null 2>&1
  docker rm -f "$CONTAINER" >/dev/null 2>&1
  docker run -d --name "$CONTAINER" --gpus all -p 8000:8000 -v "$MODELS":/models \
    ghcr.io/ggml-org/llama.cpp:server-cuda \
    -m /models/gemma-4-31B-it-hf-Q5_K_M.gguf \
    --mmproj /models/mmproj-gemma4-31b-F16.gguf \
    --host 0.0.0.0 --port 8000 -ngl 99 -c 32768 -np 1 --jinja \
    -ctk q8_0 -ctv q8_0 --alias gemma4 --reasoning-budget 20000 --tensor-split 1,1 \
    $lora_arg >/dev/null 2>&1
  server_wait 220 && { say "serving $tag"; return 0; }
  say "SERVER FAILED for $tag"; return 1
}

# pick the eval subsets: generalization prefers NEAR-MISSES (best>0) — a flatline is
# hopeless for base and adapter alike, so it can't show a lift either way.
readarray -t GEN < <(python3 - "$NG" <<'PY'
import json,sys,csv,os
p=json.load(open("/data3/tbench_local/frontier/selfdistill/moe_collect/eval_pools.json"))
best={}
f="/data3/tbench_local/frontier/selfdistill/ratchet/ratchet_evolve_results.csv"
if os.path.exists(f):
    for r in csv.DictReader(open(f)):
        try: best[r["task"]]=int(r["best"])
        except Exception: pass
gen=sorted(p["generalization"], key=lambda t: -best.get(t,0))
print("\n".join(gen[:int(sys.argv[1])]))
PY
)
readarray -t REG < <(python3 -c "
import json,sys
p=json.load(open('/data3/tbench_local/frontier/selfdistill/moe_collect/eval_pools.json'))
print('\n'.join(sorted(p['regression'])[:$NR]))")

say "EVAL sets — generalization(${#GEN[@]}): ${GEN[*]}"
say "EVAL sets — regression(${#REG[@]}): ${REG[*]}"

run_arm(){   # run_arm <base|lora>
  local arm="$1" t res passed total solved
  serve "$arm" || return 1
  for t in "${GEN[@]}" "${REG[@]}"; do
    local set=regression; printf '%s\n' "${GEN[@]}" | grep -qxF "$t" && set=generalization
    docker ps -a --format '{{.Names}}' | grep -qx "ddt_$t" && { say "SKIP $t (container busy)"; continue; }
    say "[$arm/$set] $t"
    LLM_URL=http://host.docker.internal:8000/v1 DD_VER=3.1.15 \
      bash "$SD/control_noratchet.sh" "$t" "$BUDGET" >>"$LOG" 2>&1
    # control_noratchet emits: "RESULT <task> solved=<0|1> <passed>/<total>"
    res=$(grep -E "^\[.*\] RESULT $t " "$SD/control/${t}.log" 2>/dev/null | tail -1)
    solved=$(echo "$res" | grep -oE 'solved=[01]' | cut -d= -f2); solved="${solved:-0}"
    passed=$(echo "$res" | grep -oE '[0-9]+/[0-9]+$' | cut -d/ -f1); passed="${passed:-0}"
    total=$(echo "$res"  | grep -oE '[0-9]+/[0-9]+$' | cut -d/ -f2); total="${total:-0}"
    echo "$arm,$t,$set,$passed,$total,$solved" >> "$CSV"
    say "  -> $arm/$t: $passed/$total solved=$solved"
  done
}

run_arm base
run_arm lora
say "EVAL DONE -> $CSV"
python3 - <<'PY'
import csv,collections
rows=list(csv.DictReader(open("/data3/tbench_local/frontier/selfdistill/moe_collect/eval_results.csv")))
agg=collections.defaultdict(lambda:[0,0])
for r in rows:
    k=(r["arm"],r["set"]); agg[k][0]+=int(r["solved"]); agg[k][1]+=1
print("\n=== RESULT (solved/total) ===")
for k in sorted(agg): print(f"  {k[0]:5s} {k[1]:15s} {agg[k][0]}/{agg[k][1]}")
g=lambda a,s: agg.get((a,s),[0,0])
print("\nGENERALIZATION lift (lora-base):", g("lora","generalization")[0]-g("base","generalization")[0])
print("REGRESSION delta   (lora-base):", g("lora","regression")[0]-g("base","regression")[0], "(negative = distilled DOWN)")
PY
